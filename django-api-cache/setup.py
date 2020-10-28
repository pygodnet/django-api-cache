import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="django-api-cache",
    version="0.0.1",
    author="ShuaiShuai Wang",
    author_email="shuaishuai@smxxyaq.com",
    description="A tool for django api cache with args",
    long_description=long_description,
    url="https://github.com/",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
)
