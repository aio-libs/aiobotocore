import os
import re

from setuptools import find_packages, setup

# NOTE: If updating requirements make sure to also check Pipfile for any locks
# NOTE: When updating botocore make sure to update awscli/boto3 versions below
install_requires = [
    # pegged to also match items in `extras_require`
    'botocore>=1.27.59,<1.27.60',
    'aiohttp>=3.3.1',
    'wrapt>=1.10.10',
    'aioitertools>=0.5.1',
]

extras_require = {
    'awscli': ['awscli>=1.25.60,<1.25.61'],
    'boto3': ['boto3>=1.24.59,<1.24.60'],
}


def read(f):
    return open(os.path.join(os.path.dirname(__file__), f)).read().strip()


def read_version():
    regexp = re.compile(r"^__version__\W*=\W*'([\d.abrc]+)'")
    init_py = os.path.join(
        os.path.dirname(__file__), 'aiobotocore', '__init__.py'
    )
    with open(init_py) as f:
        for line in f:
            match = regexp.match(line)
            if match is not None:
                return match.group(1)
        raise RuntimeError('Cannot find version in ' 'aiobotocore/__init__.py')


setup(
    name='aiobotocore',
    version=read_version(),
    description='Async client for aws services using botocore and aiohttp',
    long_description='\n\n'.join((read('README.rst'), read('CHANGES.rst'))),
    long_description_content_type='text/x-rst',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Intended Audience :: System Administrators',
        'Natural Language :: English',
        'License :: OSI Approved :: Apache Software License',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Environment :: Web Environment',
        'Framework :: AsyncIO',
    ],
    author="Nikolay Novik",
    author_email="nickolainovik@gmail.com",
    url='https://github.com/aio-libs/aiobotocore',
    download_url='https://pypi.python.org/pypi/aiobotocore',
    license='Apache License 2.0',
    packages=find_packages(include=['aiobotocore']),
    python_requires='>=3.7',
    install_requires=install_requires,
    extras_require=extras_require,
    include_package_data=True,
)
