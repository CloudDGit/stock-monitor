"""检查1.xls中福晶科技的数据"""
import csv

file_path = r'C:\Users\cesar\Desktop\1.xls'

with open(file_path, 'r', encoding='gbk') as f:
    sample = f.read(2048)
delimiter = '\t' if '\t' in sample else ','

rows = []
with open(file_path, 'r', encoding='gbk') as f:
    reader = csv.reader(f, delimiter=delimiter)
    for row in reader:
        rows.append(row)

headers = rows[0]
col_map = {}
for i, h in enumerate(headers):
    col_map[str(h).strip()] = i

print(f"列映射: {col_map}")
print()

for row in rows[1:]:
    name = str(row[col_map.get('证券名称', -1)]).strip()
    if '福晶' in name:
        print(f"找到: {name}")
        for col_name in ['证券代码', '证券名称', '股票余额', '盈亏比例(%)', '总盈亏', '当日盈亏', '参考成本', '市价', '当日盈亏比(%)', '市值']:
            idx = col_map.get(col_name, -1)
            val = row[idx] if idx >= 0 else 'N/A'
            print(f"  {col_name}: [{val}]")
