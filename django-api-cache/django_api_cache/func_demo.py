import hashlib
import json
import re
from django.utils.cache import _i18n_cache_key_suffix, cc_delim_re
from django.utils.decorators import decorator_from_middleware_with_args

from django.conf import settings
from django.core.cache import DEFAULT_CACHE_ALIAS, caches
from django.utils.cache import (
    get_cache_key, get_max_age, has_vary_header,
    patch_response_headers,
)
from django.utils.deprecation import MiddlewareMixin
from django.utils.encoding import iri_to_uri


def _generate_cache_header_key_my(key_prefix, request, header_key, body_data, param_data):
    """Return a cache key for the header cache."""
    url = hashlib.md5(iri_to_uri(request.build_absolute_uri()).encode('ascii'))
    cache_key = 'views.decorators.cache.cache_header.%s.%s.%s.%s.%s' % (
        key_prefix, url.hexdigest(), header_key, body_data, param_data)
    return _i18n_cache_key_suffix(request, cache_key)


def my_get_cache_key(request, key_prefix=None, method='GET', cache=None, header_key=None, body_data=None,
                     param_data=None):  # method = request.method
    """
    Return a cache key based on the request URL and query. It can be used
    in the request phase because it pulls the list of headers to take into
    account from the global URL registry and uses those to build a cache key
    to check against.

    If there isn't a headerlist stored, return None, indicating that the page
    needs to be rebuilt.
    """
    if key_prefix is None:
        key_prefix = settings.CACHE_MIDDLEWARE_KEY_PREFIX
    cache_key = _generate_cache_header_key_my(key_prefix, request, header_key, body_data, param_data=param_data)
    if cache is None:
        cache = caches[settings.CACHE_MIDDLEWARE_ALIAS]
    headerlist = cache.get(cache_key)
    if headerlist is not None:
        return _generate_cache_key_my(request, method, headerlist, key_prefix, header_key, body_data, param_data)
    else:
        return None


def _generate_cache_key_my(request, method, headerlist, key_prefix, header_key, body_data, param_data):
    """Return a cache key from the headers given in the header list."""
    ctx = hashlib.md5()
    for header in headerlist:
        value = request.META.get(header)
        if value is not None:
            ctx.update(value.encode())
    url = hashlib.md5(iri_to_uri(request.build_absolute_uri()).encode('ascii'))
    cache_key = 'views.decorators.cache.cache_api.%s.%s.%s.%s.%s.%s.%s' % (
        key_prefix, method, url.hexdigest(), ctx.hexdigest(), header_key, body_data, param_data)
    return _i18n_cache_key_suffix(request, cache_key)


def my_learn_cache_key(request, response, cache_timeout=None, key_prefix=None, cache=None, header_key=None,
                       body_data=None, param_data=None):
    """
    Learn what headers to take into account for some request URL from the
    response object. Store those headers in a global URL registry so that
    later access to that URL will know what headers to take into account
    without building the response object itself. The headers are named in the
    Vary header of the response, but we want to prevent response generation.

    The list of headers to use for cache key generation is stored in the same
    cache as the pages themselves. If the cache ages some data out of the
    cache, this just means that we have to build the response once to get at
    the Vary header and so at the list of headers to use for the cache key.
    """
    if key_prefix is None:
        key_prefix = settings.CACHE_MIDDLEWARE_KEY_PREFIX
    if cache_timeout is None:
        cache_timeout = settings.CACHE_MIDDLEWARE_SECONDS
    cache_key = _generate_cache_header_key_my(key_prefix, request, header_key, body_data, param_data)
    if cache is None:
        cache = caches[settings.CACHE_MIDDLEWARE_ALIAS]
    if response.has_header('Vary'):
        is_accept_language_redundant = settings.USE_I18N or settings.USE_L10N
        # If i18n or l10n are used, the generated cache key will be suffixed
        # with the current locale. Adding the raw value of Accept-Language is
        # redundant in that case and would result in storing the same content
        # under multiple keys in the cache. See #18191 for details.
        headerlist = []
        for header in cc_delim_re.split(response['Vary']):
            header = header.upper().replace('-', '_')
            if header != 'ACCEPT_LANGUAGE' or not is_accept_language_redundant:
                headerlist.append('HTTP_' + header)
        headerlist.sort()
        cache.set(cache_key, headerlist, cache_timeout)
        return _generate_cache_key_my(request, request.method, headerlist, key_prefix, header_key, body_data,
                                      param_data)
    else:
        # if there is no Vary header, we still need a cache key
        # for the request.build_absolute_uri()
        cache.set(cache_key, [], cache_timeout)
        return _generate_cache_key_my(request, request.method, [], key_prefix, header_key, body_data, param_data)


class MyUpdateCacheMiddleware(MiddlewareMixin):
    """
    Response-phase cache middleware that updates the cache if the response is
    cacheable.

    Must be used as part of the two-part update/fetch cache middleware.
    UpdateCacheMiddleware must be the first piece of middleware in MIDDLEWARE
    so that it'll get called last during the response phase.
    """

    def __init__(self, get_response=None):
        self.cache_timeout = settings.CACHE_MIDDLEWARE_SECONDS
        self.key_prefix = settings.CACHE_MIDDLEWARE_KEY_PREFIX
        self.cache_alias = settings.CACHE_MIDDLEWARE_ALIAS
        self.cache = caches[self.cache_alias]
        self.get_response = get_response
        self.header_key = None
        self.body_data = None
        self.param_data = None

    def _should_update_cache(self, request, response):
        return hasattr(request, '_cache_update_cache') and request._cache_update_cache

    def process_response(self, request, response):
        """Set the cache, if needed."""
        if not self._should_update_cache(request, response):
            # We don't need to update the cache, just return.
            return response

        if response.streaming or response.status_code not in (200, 304):
            return response

        # Don't cache responses that set a user-specific (and maybe security
        # sensitive) cookie in response to a cookie-less request.
        if not request.COOKIES and response.cookies and has_vary_header(response, 'Cookie'):
            return response

        # Don't cache a response with 'Cache-Control: private'
        if 'private' in response.get('Cache-Control', ()):
            return response

        # Try to get the timeout from the "max-age" section of the "Cache-
        # Control" header before reverting to using the default cache_timeout
        # length.
        timeout = get_max_age(response)
        if timeout is None:
            timeout = self.cache_timeout
        elif timeout == 0:
            # max-age was set to 0, don't bother caching.
            return response
        patch_response_headers(response, timeout)
        if timeout and response.status_code == 200:
            header_key_value = request.META.get(self.header_key, None)
            if self.body_data is True:
                try:
                    if re.search(r'json', request.environ.get('CONTENT_TYPE')):
                        body_data_value = json.loads(request.body)
                    else:
                        body_data_value = request.POST.dict()
                    if body_data_value:
                        body_data_value_hash = 'BODY_HASH_' + hashlib.md5(
                            str(body_data_value).encode('ascii')).hexdigest()
                    else:
                        body_data_value_hash = None
                except Exception as e:
                    body_data_value_hash = None
            else:
                body_data_value_hash = None

            param_data_value = None
            if self.param_data and isinstance(self.param_data, list):
                try:
                    if re.search(r'json', request.environ.get('CONTENT_TYPE')):
                        body_data_value_param = json.loads(request.body)
                    else:
                        body_data_value_param = request.POST.dict()
                    param_data_value = '-'.join(body_data_value_param.get(a, 'None') for a in self.param_data)
                except Exception as e:
                    param_data_value = None
            cache_key = my_learn_cache_key(request, response, timeout, self.key_prefix, cache=self.cache,
                                           header_key=header_key_value, body_data=body_data_value_hash,
                                           param_data=param_data_value)
            if hasattr(response, 'render') and callable(response.render):
                response.add_post_render_callback(
                    lambda r: self.cache.set(cache_key, r, timeout)
                )
            else:
                self.cache.set(cache_key, response, timeout)
        return response


class MyFetchFromCacheMiddleware(MiddlewareMixin):
    """
    Request-phase cache middleware that fetches a page from the cache.

    Must be used as part of the two-part update/fetch cache middleware.
    FetchFromCacheMiddleware must be the last piece of middleware in MIDDLEWARE
    so that it'll get called last during the request phase.
    """

    def __init__(self, get_response=None):
        self.key_prefix = settings.CACHE_MIDDLEWARE_KEY_PREFIX
        self.cache_alias = settings.CACHE_MIDDLEWARE_ALIAS
        self.cache = caches[self.cache_alias]
        self.get_response = get_response
        self.header_key = None
        self.body_data = None
        self.param_data = None

    def process_request(self, request):
        """
        Check whether the page is already cached and return the cached
        version if available.
        """
        if request.method not in ('GET', 'HEAD', 'POST', 'PUT', 'DELETE', 'PATCH'):
            request._cache_update_cache = False
            return None  # Don't bother checking the cache.
        header_key_value = request.META.get(self.header_key)

        if self.body_data is True:
            try:
                if re.search(r'json', request.environ.get('CONTENT_TYPE')):
                    body_data_value = json.loads(request.body)
                else:
                    body_data_value = request.POST.dict()
                if body_data_value:
                    body_data_value_hash = 'BODY_HASH_' + hashlib.md5(str(body_data_value).encode('ascii')).hexdigest()
                else:
                    body_data_value_hash = None
            except Exception as e:
                body_data_value_hash = None
        else:
            body_data_value_hash = None

        param_data_value = None
        if self.param_data and isinstance(self.param_data, list):
            try:
                if re.search(r'json', request.environ.get('CONTENT_TYPE')):
                    body_data_value_param = json.loads(request.body)
                else:
                    body_data_value_param = request.POST.dict()
                param_data_value = '-'.join(body_data_value_param.get(a, 'None') for a in self.param_data)
            except Exception as e:
                param_data_value = None
        cache_key = my_get_cache_key(request, self.key_prefix, request.method, cache=self.cache,
                                     header_key=header_key_value, body_data=body_data_value_hash,
                                     param_data=param_data_value)
        if cache_key is None:
            request._cache_update_cache = True
            return None  # No cache information available, need to rebuild.
        response = self.cache.get(cache_key)
        # if it wasn't found and we are looking for a HEAD, try looking just for that
        if response is None and request.method == 'HEAD':
            cache_key = get_cache_key(request, self.key_prefix, 'HEAD', cache=self.cache)
            response = self.cache.get(cache_key)

        if response is None:
            request._cache_update_cache = True
            return None  # No cache information available, need to rebuild.

        # hit, return cached response
        request._cache_update_cache = False
        return response


class MyCacheMiddleware(MyUpdateCacheMiddleware, MyFetchFromCacheMiddleware):
    """
    Cache middleware that provides basic behavior for many simple sites.

    Also used as the hook point for the cache decorator, which is generated
    using the decorator-from-middleware utility.
    """

    def __init__(self, get_response=None, cache_timeout=None, **kwargs):
        self.get_response = get_response
        self.header_key = kwargs['header_key']
        self.body_data = kwargs['body_data']
        self.param_data = kwargs['param_data']
        try:
            key_prefix = kwargs['key_prefix']
            if key_prefix is None:
                key_prefix = ''
        except KeyError:
            key_prefix = settings.CACHE_MIDDLEWARE_KEY_PREFIX
        self.key_prefix = key_prefix

        try:
            cache_alias = kwargs['cache_alias']
            if cache_alias is None:
                cache_alias = DEFAULT_CACHE_ALIAS
        except KeyError:
            cache_alias = settings.CACHE_MIDDLEWARE_ALIAS
        self.cache_alias = cache_alias

        if cache_timeout is None:
            cache_timeout = settings.CACHE_MIDDLEWARE_SECONDS
        self.cache_timeout = cache_timeout
        self.cache = caches[self.cache_alias]


def api_cache(timeout, *, cache=None, key_prefix=None, header_key=None, body_data=False, param_data=None):
    """
    接口缓存装饰器，支持主流请求方式，支持通过参数，headers内参数做缓存区分
    :param timeout:
    :param cache:
    :param key_prefix:
    :param header_key: key of request header,eg:Authorization
    :param body_data: bool-- True or False ,根据请求体缓存不同的内容
    :param param_data: list,根据请求体中的参数做缓存区分['aa','bb']
    :return:
    """
    header_key = 'HTTP_' + header_key.upper()
    return decorator_from_middleware_with_args(MyCacheMiddleware)(
        cache_timeout=timeout, cache_alias=cache, key_prefix=key_prefix, header_key=header_key, body_data=body_data,
        param_data=param_data
    )
