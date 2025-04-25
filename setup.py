# setup.py (updated to v0.1.1)
from setuptools import setup, find_packages

# read long description from README.md for PyPI
with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name='pyapptest',
    version='0.1.1',  # bumped version
    description='A Streamlit-based API testing tool',
    long_description=long_description,
    long_description_content_type='text/markdown',
    author='RePromptsQuest',
    author_email='repromptsquest@gmail.com',
    url='https://github.com/reprompts/pyapptest',
    packages=find_packages(),  # finds backend and cli
    py_modules=['ui'],         # include the top-level ui.py
    install_requires=[
        'streamlit>=1.0',
        'click>=8.0',
        'fastapi>=0.65',
        'flask>=2.0',
        'django>=3.2',
        'faker',
    ],
    entry_points={
        'console_scripts': [
            'pyapptest=cli.main:main',
        ],
    },
    include_package_data=True,
    classifiers=[
        'Programming Language :: Python :: 3',
        'Operating System :: OS Independent',
    ],
    python_requires='>=3.7',
)