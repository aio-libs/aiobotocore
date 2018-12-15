import os
import sys

from setuptools import setup, find_packages

PY_VER = sys.version_info

if not PY_VER >= (3, 5, 3):
    raise RuntimeError("aiobotocore doesn't support Python earlier than 3.5.3")


def read_gen(file_path):
    with open(os.path.join(os.getcwd(), file_path)) as f:
        yield from (x.strip() for x in f)


def read(file_path):
    with open(os.path.join(os.getcwd(), file_path)) as f:
        return f.read().strip()


def parse_req(item):
    item = item[:item.find('#')]
    item = item[:item.find(';')]
    return item.replace(' ', '')


def read_requires(file_path, subdir='requirements'):
    req = read_gen(os.path.join(subdir, file_path))
    return [parse_req(x) for x in req if x and not x.startswith('#')]


extras_require = {
    'awscli': read_requires('awscli.txt'),
    'boto3': read_requires('boto3.txt'),
}


def read_version():
    resp = read_gen(os.path.join('aiobotocore', '__init__.py'))
    try:
        v = next(x for x in resp if x.startswith('__version__'))
        return v.split('=').pop().strip().replace('"', '').replace("'", '')
    except StopIteration:
        raise RuntimeError('Cannot find version in aiobotocore/__init__.py')


classifiers = [
    'Intended Audience :: Developers',
    'Intended Audience :: System Administrators',
    'Programming Language :: Python :: 3',
    'Programming Language :: Python :: 3.5',
    'Programming Language :: Python :: 3.6',
    'Environment :: Web Environment',
    'Development Status :: 3 - Alpha',
    'Framework :: AsyncIO',
]

setup(name='aiobotocore',
      version=read_version(),
      description='Async client for aws services using botocore and aiohttp',
      long_description='\n\n'.join((read('README.rst'), read('CHANGES.txt'))),
      classifiers=classifiers,
      author="Nikolay Novik",
      author_email="nickolainovik@gmail.com",
      url='https://github.com/aio-libs/aiobotocore',
      download_url='https://pypi.python.org/pypi/aiobotocore',
      license='Apache 2',
      packages=find_packages(),
      install_requires=read_requires('_main.txt'),
      extras_require=extras_require,
      include_package_data=True)
