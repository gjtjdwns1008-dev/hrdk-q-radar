from setuptools import setup, find_packages

setup(
    name="hrdk-law-core",
    version="1.1.0",  # 종목 CSV 단일 출처 통합
    description="HRDK 법령 모니터링 공유 코어 라이브러리",
    packages=find_packages(),
    # 🌟 [종목 CSV 동봉] data 폴더의 csv가 pip install 시 함께 설치되도록 포함
    package_data={
        "hrdk_law_core": ["data/*.csv"],
    },
    include_package_data=True,
    python_requires=">=3.11",
    install_requires=[
        "requests>=2.31.0",
        "urllib3>=2.0.0",
        "gspread>=5.12.0,<7",
        "oauth2client>=4.1.3",
        "openpyxl>=3.1.2",
        "pandas>=2.0.0",
    ],
)
