import os
import re
import sys
from setuptools import setup, find_packages


# NOTE: If adding requirements make sure to also add to requirements-dev.txt
# NOTE: When updating botocore make sure to update awscli/boto3 versions below
install_requires = [
    # pegged to also match items in `extras_require`
    'botocore>=1.12.189,<1.12.190',
    'aiohttp>=3.3.1',
    'wrapt>=1.10.10',
    'async_generator>=1.10',  # can remove if we move to py3.6+
]

PY_VER = sys.version_info

if not PY_VER >= (3, 5, 3):
    raise RuntimeError("aiobotocore doesn't support Python earlier than 3.5.3")


def read(f):
    return open(os.path.join(os.path.dirname(__file__), f)).read().strip()


extras_require = {
    'awscli': ['awscli==1.16.199'],
    'boto3': ['boto3==1.9.189'],
}


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
    'Programming Language :: Python :: 3.5',
    'Programming Language :: Python :: 3.6',
    'Programming Language :: Python :: 3.7',
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
      install_requires=install_requires,
      extras_require=extras_require,
      include_package_data=True)
