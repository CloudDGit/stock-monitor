"""测试分时主力净流入API - 直接请求"""
import requests

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://data.eastmoney.com/',
}

url = 'https://push2.eastmoney.com/api/qt/stock/fflow/kline/get'
for code in ['600519', '300454', '688126']:
    if code.startswith('6') or code.startswith('5'):
        secid = f'1.{code}'
    else:
        secid = f'0.{code}'
    params = {
        'lmt': '0', 'klt': '1',
        'fields1': 'f1,f2,f3,f7',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65',
        'ut': 'b2884a393a59ad64002292a3e90d46a5',
        'secid': secid,
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        print(f"{code}: Status={r.status_code}, klines={len(r.json().get('data',{}).get('klines',[]))}")
    except Exception as e:
        print(f"{code}: {e}")