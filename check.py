import web3.eth
from web3 import Web3

node = 'http://172.31.42.147:8545'

w3 = Web3(Web3.HTTPProvider(node))
#latest = w3.eth.getBlock('latest', False)
#print(latest)

c = w3.eth.getCode("0xA696986466Ff117Ec1C5DCFFc87Eb14E720cB702")
print(c)
