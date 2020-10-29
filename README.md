# django-api-cache
# 主要用于django各类接口缓存

1. 支持'GET', 'HEAD', 'POST', 'PUT', 'DELETE', 'PATCH'，支持form_data,json</br>
2. 支持依据请求头key做缓存区分，如header_key='Authorization'，可用于传入token，区分用户等</br>
3. 支持依据请求体内容做区分，body_data=True,将根据不同的请求体做区分</br>
4. 支持请求体中参数做区分，如:param_data=['aaa','bbb']</br>

用法如下：</br>
一：安装</br>
```
pip install django-api-cache
```
二：缓存配置</br>
支持django的缓存系统

三：使用方法</br>
	1. 类视图</br>
```
class TestViewTwo(View):
    @method_decorator(api_cache(timeout=30,header_key='Authorization',body_data=False,param_data=['aaa']))
    def post(self,request):
        name = request.META.get('HTTP_AUTHORIZATION')
```
  2. ：函数</br>
```
@api_cache(timeout=30,header_key='Authorization',body_data=False,param_data=['aaa'])
def test_demo(request):
```

