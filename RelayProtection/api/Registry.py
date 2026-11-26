from api.BaseApi import BaseApi
from api.ApiDiffTest import ApiDiffTest
from api.ApiImpdTest import ApiImpdTest
from api.ApiLineTest import ApiLineTest
from api.ApiBaseTest import ApiBaseTest
from api.ApiScanTest import ApiScanTest
from api.ApiSlipTest import ApiSlipTest
from api.ApiStatSequ import ApiStatSequ
from api.ApiTimeMeas import ApiTimeMeas
from api.ApiInvsTime import ApiInvsTime

BaseApi._REGISTRY = [
    ApiDiffTest,
    ApiImpdTest,
    ApiLineTest,
    ApiBaseTest,
    ApiScanTest,
    ApiSlipTest,
    ApiStatSequ,
    ApiTimeMeas,
    ApiInvsTime,
]
