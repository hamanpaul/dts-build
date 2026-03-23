"""dts-build: Hardware-evidence-driven DTS file generator for BCM68575."""
from setuptools import setup, find_packages
from pathlib import Path

long_description = Path("SKILL.md").read_text(encoding="utf-8")

setup(
    name="dts-build",
    version="0.2.0",
    description="Generate BCM68575 DTS files from hardware evidence (schematics, GPIO tables, datasheets)",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Paul Chen",
    python_requires=">=3.10",
    packages=find_packages(exclude=["tests*"]),
    install_requires=[
        "pydantic>=2.0",
        "pyyaml>=6.0,<7.0",
        "openpyxl>=3.1,<4.0",
    ],
    extras_require={
        "agent": ["github-copilot-sdk>=0.1.0"],
        "dev": ["pytest>=7.0"],
    },
    entry_points={
        "console_scripts": [
            "dts-build=dtsbuild.cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Topic :: Software Development :: Embedded Systems",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)
