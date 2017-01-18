import os
import re
import sys
from setuptools import setup, find_packages


install_requires = ['botocore==1.5.0', 'aiohttp>=1.2.0']

PY_VER = sys.version_info

if not PY_VER >= (3, 4, 1):
    raise RuntimeError("aiobotocore doesn't support Python earlier than 3.4")


def read(f):
    return open(os.path.join(os.path.dirname(__file__), f)).read().strip()

extras_require = {}


def read_version():
    regexp = re.compile(r"^__version__\W*=\W*'([\d.abrc]+)'")
    init_py = os.path.join(os.path.dirname(__file__),
                           'aiobotocore', '__init__.py')
    with open(init_py) as f:
        for line in f:
            match = regexp.match(line)
            if match is not None:
                return match.group(1)
        else:
            raise RuntimeError('Cannot find version in '
                               'aiobotocore/__init__.py')

classifiers = [
    'Intended Audience :: Developers',
    'Intended Audience :: System Administrators',
    'Programming Language :: Python :: 3',
    'Programming Language :: Python :: 3.4',
    'Programming Language :: Python :: 3.5',
    'Environment :: Web Environment',
    'Development Status :: 3 - Alpha',
]


setup(name='aiobotocore',
      version=read_version(),
      long_description='\n\n'.join((read('README.rst'), read('CHANGES.txt'))),
      description=("Async client for AWS services using botocore and aiohttp"),
      classifiers=classifiers,
      author="Nikolay Novik",
      author_email="nickolainovik@gmail.com",
      url='https://github.com/aio-libs/aiobotocore',
      download_url='https://pypi.python.org/pypi/aiobotocore',
      license='Apache 2',
      packages=find_packages(),
      install_requires=install_requires,
      extras_require=extras_require,
      include_package_data=True)
