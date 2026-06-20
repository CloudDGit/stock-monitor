#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""验证CSV导入后数据显示逻辑"""

import json
import csv

# Step 1: 模拟CSV导入
csv_file = 'c:/Users/cesar/Desktop/1.csv'
new_stocks = []
new_positions = {}

with open(csv_file, 'r', encoding='utf-8-sig') as f:
    reader = csv.DictReader(f)
    print("CSV列名:", reader.fieldnames)
    
    for row in reader:
        code = row.get('证券代码', '').strip()
        name = row.get('证券名称', '').strip()
        
        if not code or not name:
            continue
        
        try:
            quantity = int(float(row.get('股票余额', '0').strip()))
            cost_price = float(row.get('参考成本', '0').strip())
            profit_pct = float(row.get('盈亏比例(%)', '0').strip())
            total_profit = float(row.get('总盈亏', '0').strip())
            today_profit = float(row.get('当日盈亏', '0').strip())
            today_profit_pct = float(row.get('当日盈亏比(%)', '0').strip())
            market_value = float(row.get('市值', '0').strip())
            current_price = float(row.get('市价', '0').strip())
        except Exception as e:
            print(f"  解析错误: {e}")
            continue
        
        new_stocks.append((code, name))
        new_positions[code] = {
            'name': name,
            'quantity': quantity,
            'cost_price': cost_price,
            'market_value': market_value,
            'total_profit': total_profit,
            'total_profit_percent': profit_pct,
            'today_profit': today_profit,
            'today_profit_percent': today_profit_pct,
            'current_price': current_price
        }

print(f"\n导入: {len(new_stocks)} 只股票, {len(new_positions)} 条持仓记录")

# Step 2: 模拟refresh_table逻辑
stocks = new_stocks
positions = new_positions
stock_data = {}  # 模拟API未返回数据的情况

print("\n===== 模拟 refresh_table (无实时API数据) =====")

for code, name in stocks:
    position = positions.get(code, {})
    quantity = position.get('quantity', 0)
    cost_price = position.get('cost_price', 0)
    
    # 从预存数据中获取
    current_price = position.get('current_price', 0)
    market_value = position.get('market_value', 0)
    profit_loss = position.get('total_profit', 0)
    profit_pct = position.get('total_profit_percent', 0)
    today_profit = position.get('today_profit', 0)
    today_pct = position.get('today_profit_percent', 0)
    
    # 检查实时数据（API失败时为空）
    if code in stock_data:
        data = stock_data[code]
        realtime_price = data.get('current_price', 0)
        if realtime_price > 0:
            current_price = realtime_price
    
    # 验证各字段是否>0
    print(f"\n{code} {name}:")
    print(f"  current_price={current_price:.3f} (>0? {current_price > 0})")
    print(f"  quantity={quantity} (>0? {quantity > 0})")
    print(f"  cost_price={cost_price:.3f} (>0? {cost_price > 0})")
    print(f"  market_value={market_value:,.2f} (>0? {market_value > 0})")
    print(f"  profit_loss={profit_loss:+,.2f} (!=0? {profit_loss != 0})")
    print(f"  today_profit={today_profit:+,.2f} (!=0? {today_profit != 0})")
    
    # 模拟表格显示
    if current_price > 0:
        if cost_price > 0:
            print(f"  OK: cost:{cost_price:.3f} / price:{current_price:.3f}")
        else:
            print(f"  OK: cost:-- / price:{current_price:.3f}")
    else:
        print(f"  BAD: all fields show --")

# Step 3: 保存到JSON验证
with open('c:/源码/金融/stocks.json', 'w', encoding='utf-8') as f:
    json.dump(new_stocks, f, ensure_ascii=False)

with open('c:/源码/金融/positions.json', 'w', encoding='utf-8') as f:
    json.dump(new_positions, f, ensure_ascii=False, indent=2)

# 验证保存后的文件
print("\n===== 验证保存后的文件 =====")
with open('c:/源码/金融/stocks.json', 'r', encoding='utf-8') as f:
    loaded_stocks = json.load(f)
    print(f"stocks.json: {len(loaded_stocks)} 只股票")

with open('c:/源码/金融/positions.json', 'r', encoding='utf-8') as f:
    loaded_positions = json.load(f)
    print(f"positions.json: {len(loaded_positions)} 条记录")
    
    for code, pos in loaded_positions.items():
        print(f"  {code}: quantity={pos.get('quantity')}, current_price={pos.get('current_price')}, market_value={pos.get('market_value')}")

print("\n===== 所有字段显示验证完毕 =====")
