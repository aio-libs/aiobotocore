import re
from pathlib import Path
from setuptools import find_packages, setup

_this_dir = Path(__file__).parent

# NOTE: When updating botocore make sure to update install-requires.txt
extras_require = {
    'awscli': ['awscli>=1.31.2,<1.31.14'],
    'boto3': ['boto3>=1.33.2,<1.33.14'],
}


def read(file_name: str):
    return (_this_dir / file_name).read_text().strip()


def read_requirements(file_name: str):
    return [line.strip() for line in read(file_name).splitlines() if line]


def read_version():
    regexp = re.compile(r"^__version__\W*=\W*'([\d.abrc]+)'")

    with (_this_dir / 'aiobotocore' / '__init__.py').open() as f:
        for line in f:
            if match := regexp.match(line):
                return match.group(1)

        raise RuntimeError('Cannot find version in aiobotocore/__init__.py')


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
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Environment :: Web Environment',
        'Framework :: AsyncIO',
    ],
    author="Nikolay Novik",
    author_email="nickolainovik@gmail.com",
    url='https://github.com/aio-libs/aiobotocore',
    download_url='https://pypi.python.org/pypi/aiobotocore',
    license='Apache License 2.0',
    packages=find_packages(include=['aiobotocore']),
    python_requires='>=3.8',
    install_requires=read_requirements('install-requires.txt'),
    extras_require=extras_require,
    include_package_data=True,
)
