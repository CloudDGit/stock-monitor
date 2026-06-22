#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
A股实时监控工具 - 隐蔽版 v2.0
特点:
- Excel表格样式，伪装成电子表格
- 自动刷新股票数据
- K线走势图显示
- 最小化到系统托盘
"""

# ========================
# 数值精度常量（与同花顺保持一致）
# ========================
# 盈亏金额（总盈亏/当日盈亏）：2位小数
FMT_AMOUNT = ':.2f'
# 盈亏%/涨跌幅：3位小数
FMT_PERCENT = ':.3f'
# 成本价/现价：3位小数
FMT_PRICE = ':.3f'
# 市值：2位小数
FMT_MARKET = ':.2f'

import sys
import json
import time
import threading
from datetime import datetime, timedelta
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLabel, QPushButton, QLineEdit,
                             QSystemTrayIcon, QMenu, QAction, QMessageBox,
                             QFrame, QScrollArea, QTableWidget, QTableWidgetItem,
                             QHeaderView, QDialog, QDialogButtonBox, QTabWidget,
                             QGraphicsView, QGraphicsScene, QSpinBox, QDoubleSpinBox)
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QPointF, QEvent
from PyQt5.QtGui import QIcon, QFont, QColor, QPen, QBrush, QPainter, QPainterPath, QPalette
import requests


def is_trading_time():
    """判断当前是否在A股交易时间内"""
    now = datetime.now()
    # 周末休市
    if now.weekday() >= 5:  # 5=周六, 6=周日
        return False
    t = now.time()
    from datetime import time as dtime
    # 上午 9:15 - 11:30，下午 13:00 - 15:00
    morning_start = dtime(9, 15)
    morning_end = dtime(11, 30)
    afternoon_start = dtime(13, 0)
    afternoon_end = dtime(15, 0)
    return (morning_start <= t <= morning_end) or (afternoon_start <= t <= afternoon_end)


def compute_position_profit(code, position, stock_data_entry):
    """
    统一计算某只股票当前的总盈亏、盈亏%、当日盈亏、当日盈亏%。

    规则：
    1. 若 is_sold=True（清仓股）：使用导入/卖出时锁定的 total_profit / today_profit，不再重新计算
       （quantity=0 时若按公式会得到 0，会把真实盈亏冲掉）
    2. 非交易时间：使用导入文件中的原始盈亏数据（positions 中的 total_profit / today_profit 等）
       显示用券商导出值，不做任何公式计算
    3. 交易时间：按实时行情动态计算
       - 若 is_today_added=True（当日新开 / 当日增买）：
         总盈亏 = 当日盈亏 = (现价 - 成本价) * 数量
       - 普通持仓：
         总盈亏 = (现价 - 成本价) * 数量
         当日盈亏 = 当日涨跌额 * 数量
    """
    # 基本字段
    quantity = position.get('quantity', 0)
    cost_price = position.get('cost_price', 0)
    current_price = position.get('current_price', 0)
    prev_close = position.get('prev_close', 0)
    change = 0
    change_percent = 0

    # 优先从实时行情更新现价/涨跌（用于显示当前价格）
    if stock_data_entry is not None:
        rt_price = stock_data_entry.get('current_price', 0)
        if rt_price > 0:
            current_price = rt_price
        change = stock_data_entry.get('change', 0)
        change_percent = stock_data_entry.get('change_percent', 0)
        # 优先从行情数据获取昨收价（更准确）
        rt_prev_close = stock_data_entry.get('prev_close', 0)
        if rt_prev_close > 0:
            prev_close = rt_prev_close
        elif current_price > 0 and change != 0:
            prev_close = current_price - change

    is_sold = position.get('is_sold', False)

    # -------- 规则 1：已卖出（清仓）股，始终使用锁定值 --------
    if is_sold:
        return {
            'quantity': quantity,
            'cost_price': cost_price,
            'current_price': current_price,
            'market_value': position.get('market_value', 0.0),
            'total_profit': position.get('total_profit', 0),
            'total_profit_percent': position.get('total_profit_percent', 0),
            'today_profit': position.get('today_profit', 0),
            'today_profit_percent': position.get('today_profit_percent', 0),
            'change_percent': change_percent,
            'is_sold': True,
        }

    in_trading = is_trading_time()

    # -------- 规则 2：非交易时间，优先使用实时行情重新计算 --------
    if not in_trading:
        market_value = position.get('market_value', 0.0)
        total_profit = position.get('total_profit', 0)
        total_profit_pct = position.get('total_profit_percent', 0)
        today_profit = position.get('today_profit', 0)
        today_profit_pct = position.get('today_profit_percent', 0)

        # 如果有实时行情数据，用公式重新计算（确保价格变化后盈亏同步更新）
        if quantity > 0 and cost_price > 0 and current_price > 0:
            # 重新计算总盈亏
            total_profit = (current_price - cost_price) * quantity
            total_profit_pct = (total_profit / (cost_price * quantity)) * 100 if cost_price * quantity > 0 else 0
            
            # 重新计算当日盈亏
            is_today_added = position.get('is_today_added', False)
            if is_today_added:
                today_profit = total_profit
                today_profit_pct = total_profit_pct
            else:
                if prev_close > 0 and current_price > 0:
                    change_val = current_price - prev_close
                else:
                    change_val = change
                today_profit = change_val * quantity if quantity > 0 else 0
                today_profit_pct = change_percent

        return {
            'quantity': quantity,
            'cost_price': cost_price,
            'current_price': current_price,
            'market_value': market_value if market_value > 0 else (quantity * current_price),
            'total_profit': total_profit,
            'total_profit_percent': total_profit_pct,
            'today_profit': today_profit,
            'today_profit_percent': today_profit_pct,
            'change_percent': change_percent,
            'is_sold': False,
        }

    # -------- 规则 3：交易时间，按实时行情动态计算 --------
    # 计算市值、总盈亏
    market_value = quantity * current_price if (quantity > 0 and current_price > 0) else 0
    total_profit = 0
    total_profit_pct = 0
    if quantity > 0 and cost_price > 0 and current_price > 0:
        total_profit = (current_price - cost_price) * quantity
        total_profit_pct = (total_profit / (cost_price * quantity)) * 100 if cost_price * quantity > 0 else 0

    # 计算当日盈亏
    is_today_added = position.get('is_today_added', False)
    if is_today_added:
        today_profit = total_profit
        today_profit_pct = total_profit_pct
    else:
        if prev_close > 0 and current_price > 0:
            change_val = current_price - prev_close
        else:
            change_val = change
        today_profit = change_val * quantity if quantity > 0 else 0
        today_profit_pct = change_percent

    return {
        'quantity': quantity,
        'cost_price': cost_price,
        'current_price': current_price,
        'market_value': market_value,
        'total_profit': total_profit,
        'total_profit_percent': total_profit_pct,
        'today_profit': today_profit,
        'today_profit_percent': today_profit_pct,
        'change_percent': change_percent,
        'is_sold': False,
    }


class StockDataFetcher(QThread):
    """股票数据获取线程"""
    data_updated = pyqtSignal(dict)
    
    def __init__(self, stock_codes):
        super().__init__()
        self.stock_codes = stock_codes
        self.running = True
    
    def run(self):
        while self.running:
            try:
                data = self.fetch_stock_data()
                self.data_updated.emit(data)
            except Exception as e:
                print(f"获取数据失败: {e}")
            time.sleep(10)  # 10秒刷新一次

    def fetch_and_emit(self):
        """手动触发一次数据获取并发射信号"""
        try:
            data = self.fetch_stock_data()
            self.data_updated.emit(data)
        except Exception as e:
            print(f"手动获取数据失败: {e}")

    def fetch_stock_data(self):
        """从腾讯财经获取股票数据"""
        # 生成股票代码列表
        codes_list = []
        for code in self.stock_codes:
            if code.startswith('HK') or code.startswith('hk'):
                # 港股 - 腾讯API需要5位数字，不足补零
                hk_code = code[2:].zfill(5)
                codes_list.append(f'hk{hk_code}')
            elif code.startswith('5') or code.startswith('6'):
                # 上海（含ETF）
                codes_list.append(f'sh{code}')
            else:
                # 深圳
                codes_list.append(f'sz{code}')
        codes = ','.join(codes_list)

        url = f'https://qt.gtimg.cn/q={codes}'

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://finance.qq.com'
        }

        response = requests.get(url, headers=headers, timeout=10)
        response.encoding = 'gbk'

        result = {}
        lines = response.text.strip().split('\n')

        for line in lines:
            if '=' in line:
                parts = line.split('=')
                if len(parts) == 2:
                    # 解析股票代码，如 v_sh600519 或 v_hk02513
                    code_part_full = parts[0].replace('v_', '')
                    # 判断市场前缀
                    if code_part_full.startswith('sh') or code_part_full.startswith('sz'):
                        code_part = code_part_full[2:]
                    elif code_part_full.startswith('hk'):
                        # 港股代码去掉前导零，保持与用户输入一致
                        code_part = f'HK{code_part_full[2:].lstrip("0")}'
                    else:
                        continue

                    data_part = parts[1].strip('";').split('~')

                    # 腾讯API返回的数据字段：
                    # 0: 未知, 1: 名称, 2: 代码, 3: 当前价格, 4: 昨收, 5: 今开,
                    # 6: 成交量(手), 7: 外盘, 8: 内盘, 9: 最高, 10: 最低, ...
                    # 30: 时间, 31: 涨跌, 32: 涨跌%, ...
                    if len(data_part) > 32:
                        try:
                            current_price_str = data_part[3]
                            prev_close_str = data_part[4]
                            high_str = data_part[33] if len(data_part) > 33 else data_part[9]
                            low_str = data_part[34] if len(data_part) > 34 else data_part[10]

                            # 处理价格数据（可能为空或非数字）
                            current_price = float(current_price_str) if current_price_str and current_price_str.replace('.', '').replace('-', '').isdigit() else 0
                            prev_close = float(prev_close_str) if prev_close_str and prev_close_str.replace('.', '').replace('-', '').isdigit() else 0
                            high = float(high_str) if high_str and high_str.replace('.', '').replace('-', '').isdigit() else 0
                            low = float(low_str) if low_str and low_str.replace('.', '').replace('-', '').isdigit() else 0

                            if current_price > 0:
                                # 成交额：腾讯API field 37 为成交额(万元)
                                amount_val = 0.0
                                if len(data_part) > 37:
                                    try:
                                        amount_val = float(data_part[37]) * 10000  # 转为元
                                    except:
                                        amount_val = 0.0

                                stock_info = {
                                    'name': data_part[1],
                                    'open_price': float(data_part[5]) if data_part[5] else 0,
                                    'prev_close': prev_close,
                                    'current_price': current_price,
                                    'high': high,
                                    'low': low,
                                    'volume': int(float(data_part[6])) if data_part[6] else 0,
                                    'amount': amount_val,
                                }

                                # 计算涨跌
                                change = stock_info['current_price'] - stock_info['prev_close']
                                change_percent = (change / stock_info['prev_close']) * 100 if stock_info['prev_close'] > 0 else 0

                                stock_info['change'] = change
                                stock_info['change_percent'] = change_percent

                                result[code_part] = stock_info
                        except Exception as e:
                            print(f"解析股票 {code_part} 数据失败: {e}")
                            pass

        return result
    
    def stop(self):
        self.running = False


class StockChartWidget(QWidget):
    """股票走势图组件（仿同花顺分时图样式）"""
    def __init__(self, stock_code, parent=None):
        super().__init__(parent)
        self.stock_code = stock_code
        self.stock_name = ""
        self.price_history = []  # 价格历史 [(timestamp, price), ...]
        self.minute_data = []    # 分时数据 [{time, price, volume}, ...]
        self.max_history = 100   # 最多保存100个数据点
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ===== 顶部信息栏（仿同花顺）=====
        self.info_bar = QFrame()
        self.info_bar.setStyleSheet("background: #1a1a1a; color: white; padding: 4px;")
        self.info_bar.setFixedHeight(40)
        info_layout = QHBoxLayout(self.info_bar)
        info_layout.setContentsMargins(8, 0, 8, 0)
        info_layout.setSpacing(12)

        self.title_label = QLabel("未选择股票")
        self.title_label.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        self.title_label.setStyleSheet("color: #ffffff;")
        info_layout.addWidget(self.title_label)

        self.price_label = QLabel("--")
        self.price_label.setFont(QFont("Consolas", 12, QFont.Bold))
        self.price_label.setMinimumWidth(90)
        info_layout.addWidget(self.price_label)

        self.pct_label = QLabel("--")
        self.pct_label.setFont(QFont("Consolas", 10, QFont.Bold))
        info_layout.addWidget(self.pct_label)

        self.change_label = QLabel("--")
        self.change_label.setFont(QFont("Consolas", 10))
        info_layout.addWidget(self.change_label)

        self.high_label = QLabel("最高 --")
        self.high_label.setFont(QFont("Consolas", 10))
        self.high_label.setStyleSheet("color: #ff9999;")
        info_layout.addWidget(self.high_label)

        self.low_label = QLabel("最低 --")
        self.low_label.setFont(QFont("Consolas", 10))
        self.low_label.setStyleSheet("color: #99ccff;")
        info_layout.addWidget(self.low_label)

        self.vol_label = QLabel("成交量 --")
        self.vol_label.setFont(QFont("Consolas", 10))
        self.vol_label.setStyleSheet("color: #eeeeee;")
        info_layout.addWidget(self.vol_label)

        self.amt_label = QLabel("成交额 --")
        self.amt_label.setFont(QFont("Consolas", 10))
        self.amt_label.setStyleSheet("color: #eeeeee;")
        info_layout.addWidget(self.amt_label)

        info_layout.addStretch()
        layout.addWidget(self.info_bar)

        # ===== 图表区域（占更多空间）=====
        self.chart_view = ChartView(self)
        self.chart_view.setMinimumHeight(360)
        layout.addWidget(self.chart_view, 1)

    def update_price(self, price):
        """更新价格（简单走势）"""
        timestamp = datetime.now()
        self.price_history.append((timestamp, price))
        if len(self.price_history) > self.max_history:
            self.price_history = self.price_history[-self.max_history:]
        self.chart_view.update_chart(self.price_history)

    def set_minute_data(self, data, real_data=None):
        """设置并绘制分时数据

        Args:
            data: 分时数据列表
            real_data: 真实行情数据字典（含 high/low/volume/amount），用于信息栏显示
        """
        if not data:
            return
        self.minute_data = data

        # 更新图表
        self.chart_view.draw_minute_chart(data)
        # 存储数据并预计算价格区间供鼠标悬停使用
        self.chart_view._minute_data = data
        self.chart_view._prev_close = data[0].get('prev_close', data[0]['price'])
        self.chart_view._cache_price_range()

        # ===== 更新顶部信息栏
        latest = data[-1]
        prices = [d['price'] for d in data]
        first_price = data[0]['price']
        prev_close = data[0].get('prev_close', first_price)
        current_price = latest['price']

        # 优先使用真实行情数据，没有时回退到模拟数据
        if real_data:
            high_price = real_data.get('high', 0) or max(prices)
            low_price = real_data.get('low', 0) or min(prices)
            total_vol = real_data.get('volume', 0)
            total_amt = real_data.get('amount', 0)
            # 如果真实数据为0，回退到模拟数据
            if total_vol == 0:
                total_vol = sum([d.get('volume', 0) for d in data])
            if total_amt == 0:
                total_amt = sum([d.get('amount', d.get('volume', 0) * d['price']) for d in data])
        else:
            high_price = max(prices)
            low_price = min(prices)
            total_vol = sum([d.get('volume', 0) for d in data])
            total_amt = sum([d.get('amount', d.get('volume', 0) * d['price']) for d in data])

        is_up = current_price >= prev_close
        change = current_price - prev_close
        change_pct = (change / prev_close * 100) if prev_close > 0 else 0

        # 标题
        code_text = f"{self.stock_name} {self.stock_code}"
        self.title_label.setText(code_text)

        # 当前价
        self.price_label.setText(f"{current_price:.3f}")
        if is_up:
            self.price_label.setStyleSheet("color: #ff5555; font-weight: bold;")
            self.pct_label.setStyleSheet("color: #ff5555; font-weight: bold;")
            self.change_label.setStyleSheet("color: #ff5555; font-weight: bold;")
        else:
            self.price_label.setStyleSheet("color: #52c41a; font-weight: bold;")
            self.pct_label.setStyleSheet("color: #52c41a; font-weight: bold;")
            self.change_label.setStyleSheet("color: #52c41a; font-weight: bold;")

        self.pct_label.setText(f"{change_pct:+.2f}%")
        self.change_label.setText(f"{change:+.3f}")
        self.high_label.setText(f"最高 {high_price:.3f}")
        self.high_label.setStyleSheet("color: #ff9999; font-weight: bold;")
        self.low_label.setText(f"最低 {low_price:.3f}")
        self.low_label.setStyleSheet("color: #99ccff; font-weight: bold;")
        # 成交量：API返回的是手(100股)，转为股
        self.vol_label.setText(f"成交量 {total_vol/10000:.2f}万手")
        self.vol_label.setStyleSheet("color: #ffffff;")
        self.amt_label.setText(f"成交额 {total_amt/100000000:.2f}亿")
        self.amt_label.setStyleSheet("color: #ffffff;")


class ChartView(QGraphicsView):
    """图表视图（同花顺风格分时图）"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.Antialiasing)
        self.setMinimumSize(400, 300)
        # 白色背景，黑色细边框
        self.setStyleSheet("background: white; border: 1px solid #e0e0e0;")
        # 鼠标追踪
        self.setMouseTracking(True)
        self._minute_data = None
        self._prev_close = 0
        # 缓存的预计算值（数据不变时不重复计算）
        self._cached_prices = []
        self._cached_min_price = 0
        self._cached_max_price = 0
        self._cached_price_range = 0
        self._cached_price_h = 0
        self._last_mouse_idx = -1  # 上次鼠标对应的数据点索引
        # 十字光标+提示面板
        self._cross_v = None
        self._cross_h = None
        self._tooltip_bg = None
        self._tooltip_texts = []  # 复用文本项，避免频繁创建/销毁导致崩溃

    def _ensure_crosshair_items(self):
        """确保十字光标元素存在（场景清空后需重建，文本项复用）"""
        scene = self.scene()
        if self._cross_v is None or self._cross_v.scene() != scene:
            self._cross_v = scene.addLine(0, 0, 0, 0, QPen(QColor('#666'), 1, Qt.DashLine))
            self._cross_v.hide()
        if self._cross_h is None or self._cross_h.scene() != scene:
            self._cross_h = scene.addLine(0, 0, 0, 0, QPen(QColor('#666'), 1, Qt.DashLine))
            self._cross_h.hide()
        if self._tooltip_bg is None or self._tooltip_bg.scene() != scene:
            self._tooltip_bg = scene.addRect(0, 0, 140, 95, QPen(QColor('#ccc'), 1), QBrush(QColor(255, 255, 255, 240)))
            self._tooltip_bg.hide()
        # 文本项：首次创建5行，后续复用
        if not self._tooltip_texts:
            for i in range(5):
                t = scene.addText("")
                t.setFont(QFont("Consolas", 9))
                t.hide()
                self._tooltip_texts.append(t)

    def _cache_price_range(self):
        """预计算价格区间（数据更新时调用一次，避免每次鼠标移动都重复计算）"""
        if not self._minute_data:
            return
        self._cached_prices = [p['price'] for p in self._minute_data]
        real_min, real_max = min(self._cached_prices), max(self._cached_prices)
        price_range_raw = real_max - real_min
        min_range = abs(self._prev_close) * 0.03
        if price_range_raw < min_range:
            price_range_raw = min_range
        padding = price_range_raw * 0.12
        self._cached_max_price = real_max + padding
        self._cached_min_price = real_min - padding
        if self._prev_close > self._cached_max_price:
            self._cached_max_price = self._prev_close * 1.002
        if self._prev_close < self._cached_min_price:
            self._cached_min_price = self._prev_close * 0.998
        self._cached_price_range = self._cached_max_price - self._cached_min_price
        # price_h 需要根据当前视图大小计算，这里不缓存

    def keyPressEvent(self, event):
        """Esc键返回主页面"""
        if event.key() == Qt.Key_Escape:
            main_window = self.window()
            if hasattr(main_window, 'tab_widget'):
                main_window.tab_widget.setCurrentIndex(0)
        else:
            super().keyPressEvent(event)

    def mouseMoveEvent(self, event):
        """鼠标移动时显示十字光标和数据面板"""
        super().mouseMoveEvent(event)
        if not self._minute_data:
            return

        try:
            pos = event.pos()
            scene = self.scene()
            if not scene:
                return

            w, h = self.width(), self.height()
            pad_l, pad_r = 65, 85
            price_top, price_bottom = 8, int(h * 0.66)
            plot_w = w - pad_l - pad_r
            n_points = len(self._minute_data)

            # 判断鼠标是否在价格图区域内
            x = pos.x()
            if x < pad_l or x > w - pad_r:
                self._hide_crosshair()
                return

            # 找到最近的数据点
            idx = int((x - pad_l) / plot_w * (n_points - 1) + 0.5)
            idx = max(0, min(n_points - 1, idx))

            # 数据点没变则跳过（避免重复绘制）
            if idx == self._last_mouse_idx:
                return
            self._last_mouse_idx = idx

            d = self._minute_data[idx]
            pt_x = pad_l + (idx / max(n_points - 1, 1)) * plot_w

            # 使用缓存的价格区间计算Y坐标
            price_h = price_bottom - price_top
            pt_y = price_bottom - ((d['price'] - self._cached_min_price) / self._cached_price_range) * price_h if self._cached_price_range > 0 else price_bottom

            self._ensure_crosshair_items()

            # 竖线：从价格区顶部到成交量区底部
            vol_bottom = h - 18
            self._cross_v.setLine(pt_x, price_top, pt_x, vol_bottom)
            self._cross_v.show()
            # 横线：从左到右
            self._cross_h.setLine(pad_l, pt_y, w - pad_r, pt_y)
            self._cross_h.show()

            # 提示面板
            self._tooltip_bg.show()
            tip_x = pt_x + 12
            if tip_x + 140 > w - 5:
                tip_x = pt_x - 152
            tip_y = price_top + 5
            tip_w, tip_h = 150, 100
            self._tooltip_bg.setRect(tip_x, tip_y, tip_w, tip_h)

            # 构建提示文本
            time_str = d.get('time', '')
            price = d['price']
            change = price - self._prev_close
            change_pct = (change / self._prev_close * 100) if self._prev_close > 0 else 0
            vol = d.get('volume', 0)

            lines = [
                f"时间: {time_str}",
                f"价格: {price:.2f}",
                f"涨跌: {change:+.2f}",
                f"涨幅: {change_pct:+.2f}%",
                f"成交量: {vol}手",
            ]

            text_color = QColor('#d32f2f') if change >= 0 else QColor('#388e3c')
            line_h = 18
            for i, line in enumerate(lines):
                t = self._tooltip_texts[i]
                t.setPlainText(line)
                t.setDefaultTextColor(text_color if i >= 2 else QColor('#333'))
                t.setPos(tip_x + 6, tip_y + 5 + i * line_h)
                t.show()
        except Exception:
            pass  # 场景刷新期间可能暂时失效，忽略

    def _hide_crosshair(self):
        """隐藏十字光标"""
        self._last_mouse_idx = -1  # 重置鼠标位置缓存
        if self._cross_v:
            self._cross_v.hide()
        if self._cross_h:
            self._cross_h.hide()
        if self._tooltip_bg:
            self._tooltip_bg.hide()
        for t in self._tooltip_texts:
            t.hide()

    def leaveEvent(self, event):
        """鼠标离开时隐藏"""
        self._hide_crosshair()
        super().leaveEvent(event)

    def resizeEvent(self, event):
        """视图变化时同步刷新场景大小"""
        super().resizeEvent(event)
        scene = self.scene()
        if scene:
            scene.setSceneRect(0, 0, self.width(), self.height())

    def update_chart(self, price_history):
        """更新简单价格趋势图（退化为简单折线）"""
        if not price_history:
            return
        scene = self.scene()
        scene.clear()
        w, h = self.width(), self.height()
        pad_l, pad_r, pad_t, pad_b = 60, 20, 10, 10
        prices = [p[1] for p in price_history]
        min_p, max_p = min(prices), max(prices)
        rng = max_p - min_p if max_p > min_p else 1.0
        pen_axis = QPen(QColor('#e0e0e0'), 1)
        for i in range(5):
            y = pad_t + i * (h - pad_t - pad_b) / 4.0
            scene.addLine(pad_l, y, w - pad_r, y, pen_axis)
            pv = max_p - (i / 4) * rng
            t = scene.addText(f"{pv:.3f}")
            t.setDefaultTextColor(QColor('#999'))
            t.setFont(QFont("Consolas", 8))
            t.setPos(4, y - 8)
        if len(price_history) > 1:
            path = QPainterPath()
            for i, (_, price) in enumerate(price_history):
                x = pad_l + (i / max(len(price_history) - 1, 1)) * (w - pad_l - pad_r)
                y = h - pad_b - ((price - min_p) / rng) * (h - pad_t - pad_b)
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            color = '#d32f2f' if price_history[-1][1] >= price_history[0][1] else '#388e3c'
            pen_line = QPen(QColor(color), 2)
            scene.addPath(path, pen_line)

    def draw_minute_chart(self, minute_data):
        """绘制同花顺风格分时图：价格曲线 + 面积填充 + 成交量柱"""
        if not minute_data:
            return
        scene = self.scene()
        scene.clear()
        # 重置十字光标引用（场景清空后旧引用指向已删除对象，需置空）
        self._cross_v = None
        self._cross_h = None
        self._tooltip_bg = None
        self._tooltip_texts = []

        w = max(self.width(), 400)
        h = max(self.height(), 300)
        scene.setSceneRect(0, 0, w, h)

        # ===== 区域划分：上方价格区(68%)，下方成交量区(32%) =====
        pad_l, pad_r = 65, 85  # 右侧留足空间给百分比标签
        price_top, price_bottom = 8, int(h * 0.66)
        vol_top, vol_bottom = price_bottom + 16, h - 18
        plot_w = w - pad_l - pad_r

        # ===== 数据准备 =====
        prices = [d['price'] for d in minute_data]
        first_price = prices[0]
        prev_close = minute_data[0].get('prev_close', first_price)

        # 价格显示区间：动态计算，基于实际最高/最低价，上下留padding（同花顺逻辑）
        real_min, real_max = min(prices), max(prices)
        price_range_raw = real_max - real_min
        # 最小范围：至少昨收价的±1.5%
        min_range = abs(prev_close) * 0.03
        if price_range_raw < min_range:
            price_range_raw = min_range
        # 上下各扩展12% padding
        padding = price_range_raw * 0.12
        max_price = real_max + padding
        min_price = real_min - padding
        # 确保昨收价在区间内
        if prev_close > max_price:
            max_price = prev_close * 1.002
        if prev_close < min_price:
            min_price = prev_close * 0.998

        price_range = max_price - min_price
        price_h = price_bottom - price_top

        # ===== 绘制细网格（浅灰）=====
        grid_pen = QPen(QColor('#f0f0f0'), 1)
        # 水平网格线 - 4条（5个价格区间，同花顺标准）
        n_h_lines = 5  # 5个价格刻度 = 4条网格线
        for i in range(n_h_lines):
            y = price_top + (i / (n_h_lines - 1.0)) * price_h
            scene.addLine(pad_l, y, w - pad_r, y, grid_pen)

            # 左侧价格标签
            pv = max_price - (i / (n_h_lines - 1)) * price_range
            pt = scene.addText(f"{pv:.2f}")
            pt.setDefaultTextColor(QColor('#666'))
            pt.setFont(QFont("Consolas", 8))
            pt.setPos(4, y - 8)

            # 右侧百分比标签
            pct = ((pv - prev_close) / prev_close) * 100 if prev_close > 0 else 0
            pct_text = scene.addText(f"{pct:+.2f}%")
            if abs(pct) < 0.001:
                pct_text.setDefaultTextColor(QColor('#888'))
            elif pct > 0:
                pct_text.setDefaultTextColor(QColor('#d32f2f'))
            else:
                pct_text.setDefaultTextColor(QColor('#388e3c'))
            pct_text.setFont(QFont("Consolas", 8))
            pct_text.setPos(w - pad_r + 4, y - 8)

        # 垂直时间线 - 4个时间刻度（同花顺标准：5个标签）
        # 9:30, 10:30, 11:30/13:00, 14:00, 15:00
        time_points = ['9:30', '10:30', '11:30/13:00', '14:00', '15:00']
        n_time_points = len(time_points)
        for i, label in enumerate(time_points):
            x = pad_l + (i / (n_time_points - 1.0)) * plot_w
            # 垂直网格线（价格区 + 成交量区）
            scene.addLine(x, price_top, x, price_bottom, grid_pen)
            scene.addLine(x, vol_top, x, vol_bottom, grid_pen)

            # 时间标签
            tt = scene.addText(label)
            tt.setDefaultTextColor(QColor('#999'))
            tt.setFont(QFont("Microsoft YaHei", 8))
            tt.setPos(x - 22, vol_bottom + 2)

        # ===== 昨收价参考线（浅灰虚线）=====
        if min_price < prev_close < max_price:
            prev_y = price_bottom - ((prev_close - min_price) / price_range) * price_h
            prev_pen = QPen(QColor('#bfbfbf'), 1, Qt.DashLine)
            scene.addLine(pad_l, prev_y, w - pad_r, prev_y, prev_pen)

            # 昨收价文字标签
            prev_lbl = scene.addText(f"昨收{prev_close:.3f}")
            prev_lbl.setDefaultTextColor(QColor('#888'))
            prev_lbl.setFont(QFont("Consolas", 8))
            prev_lbl.setPos(w - pad_r - 65, prev_y - 16)

        # ===== 绘制价格曲线 + 面积填充 =====
        n_points = len(minute_data)
        # 先计算每个点的坐标
        price_coords = []
        for i, d in enumerate(minute_data):
            x = pad_l + (i / max(n_points - 1, 1)) * plot_w
            y = price_bottom - ((d['price'] - min_price) / price_range) * price_h
            # 裁剪到可视区
            y = max(price_top, min(price_bottom, y))
            price_coords.append((x, y))

        # 面积填充（相对于昨收价）
        if len(price_coords) > 1:
            prev_y = price_bottom - ((prev_close - min_price) / price_range) * price_h
            prev_y = max(price_top, min(price_bottom, prev_y))

            # 分两段：高于昨收（红）和低于昨收（绿）
            up_path = QPainterPath()
            down_path = QPainterPath()
            started_up, started_down = False, False

            for i, (x, y) in enumerate(price_coords):
                # 判断是上还是下（基于上一个点）
                above = y < prev_y  # y越小越靠上
                if above:
                    if not started_up:
                        # 如果上一个点在下，则从prev_y开始
                        if i > 0 and price_coords[i-1][1] >= prev_y:
                            # 线性插值找交点
                            px0, py0 = price_coords[i-1]
                            if py0 != y:
                                interp_x = px0 + (prev_y - py0) * (x - px0) / (y - py0) if (y - py0) != 0 else x
                            else:
                                interp_x = x
                            up_path.moveTo(interp_x, prev_y)
                        else:
                            up_path.moveTo(x, prev_y)
                        started_up = True
                    up_path.lineTo(x, y)
                    # 如果下一个点低于昨收价，则添加到昨收价
                    if i < len(price_coords) - 1 and price_coords[i+1][1] > prev_y:
                        px1, py1 = price_coords[i+1]
                        if py1 != y:
                            interp_x = x + (prev_y - y) * (px1 - x) / (py1 - y)
                        else:
                            interp_x = x
                        up_path.lineTo(interp_x, prev_y)
                else:
                    if not started_down:
                        if i > 0 and price_coords[i-1][1] < prev_y:
                            px0, py0 = price_coords[i-1]
                            if py0 != y:
                                interp_x = px0 + (prev_y - py0) * (x - px0) / (y - py0) if (y - py0) != 0 else x
                            else:
                                interp_x = x
                            down_path.moveTo(interp_x, prev_y)
                        else:
                            down_path.moveTo(x, prev_y)
                        started_down = True
                    down_path.lineTo(x, y)
                    if i < len(price_coords) - 1 and price_coords[i+1][1] < prev_y:
                        px1, py1 = price_coords[i+1]
                        if py1 != y:
                            interp_x = x + (prev_y - y) * (px1 - x) / (py1 - y)
                        else:
                            interp_x = x
                        down_path.lineTo(interp_x, prev_y)

            # 填充区域（回到昨收线形成闭合）
            if started_up:
                last_x_up = price_coords[-1][0]
                up_path.lineTo(last_x_up, prev_y)
                up_brush = QBrush(QColor(255, 80, 80, 40))
                scene.addPath(up_path, QPen(Qt.NoPen), up_brush)
            if started_down:
                last_x_down = price_coords[-1][0]
                down_path.lineTo(last_x_down, prev_y)
                down_brush = QBrush(QColor(80, 200, 100, 40))
                scene.addPath(down_path, QPen(Qt.NoPen), down_brush)

            # 绘制价格曲线本身（白色或黄色）
            line_path = QPainterPath()
            for i, (x, y) in enumerate(price_coords):
                if i == 0:
                    line_path.moveTo(x, y)
                else:
                    line_path.lineTo(x, y)
            # 同花顺风格：深色线（白色背景上清晰可见）
            pen_line = QPen(QColor('#333333'), 1.5)
            scene.addPath(line_path, pen_line)

            # 最后一个点绘制小圆点和数值
            last_x, last_y = price_coords[-1]
            last_price_val = minute_data[-1]['price']
            pen_dot = QPen(QColor('#333333'), 3)
            scene.addEllipse(last_x - 3, last_y - 3, 6, 6, pen_dot)

            # 右侧数值标签（白底深色字）
            label_text = f"{last_price_val:.3f}"
            lbl = scene.addText(label_text)
            lbl_color = '#d32f2f' if last_price_val >= prev_close else '#388e3c'
            lbl.setDefaultTextColor(QColor(lbl_color))
            lbl.setFont(QFont("Consolas", 9, QFont.Bold))
            lbl.setPos(w - pad_r + 4, last_y - 8)

        # ===== 绘制成交量柱（同花顺风格：细线柱，红涨绿跌） =====
        volumes = [d.get('volume', 0) for d in minute_data]
        max_vol = max(volumes) if volumes and max(volumes) > 0 else 1
        vol_h = vol_bottom - vol_top

        # 柱宽：按点间距计算，每个柱子占满间距，同花顺风格
        bar_w = plot_w / max(n_points, 1)
        if bar_w < 1:
            bar_w = 1

        for i, d in enumerate(minute_data):
            x = pad_l + (i / max(n_points - 1, 1)) * plot_w
            v = d.get('volume', 0)
            vh = (v / max_vol) * vol_h if max_vol > 0 else 0

            if vh <= 0:
                continue

            # 颜色：当前价 >= 上一价 用红，否则绿
            if i > 0:
                is_up = d['price'] >= minute_data[i-1]['price']
            else:
                is_up = d['price'] >= prev_close

            if is_up:
                bar_color = QColor('#e53935')
            else:
                bar_color = QColor('#43a047')

            # 同花顺风格：不重叠的细柱，每个柱子之间无间隙
            scene.addRect(x - bar_w / 2, vol_bottom - vh, bar_w, vh,
                          QPen(Qt.NoPen), QBrush(bar_color))

        # 成交量标签（显示总成交量）
        total_vol = sum(volumes)
        vol_lbl = scene.addText(f"量 {total_vol/10000:.0f}万")
        vol_lbl.setDefaultTextColor(QColor('#999'))
        vol_lbl.setFont(QFont("Consolas", 8))
        vol_lbl.setPos(4, vol_top - 2)


class MinimalistPanel(QWidget):
    """极简模式面板 - 只显示股票名称和涨跌百分比，支持鼠标拖动"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.stock_labels = []  # 存储股票标签
        self._drag_pos = None  # 拖动起始位置
        # 必须作为独立窗口，不能有parent（否则父窗口hide时子窗口也会隐藏）
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_QuitOnClose, False)  # 关闭时不退出程序
        self.init_ui()

    def mousePressEvent(self, event):
        """记录鼠标按下位置，用于拖动窗口"""
        if event.button() == Qt.LeftButton:
            # 确保捕获所有子控件的鼠标事件
            self._drag_pos = event.globalPos()
            self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """拖动窗口"""
        if self._drag_pos is not None and event.buttons() == Qt.LeftButton:
            delta = event.globalPos() - self._drag_pos
            new_x = self.x() + delta.x()
            new_y = self.y() + delta.y()
            # 限制在屏幕范围内
            screen_rect = QApplication.desktop().screenGeometry()
            new_x = max(0, min(new_x, screen_rect.width() - self.width()))
            new_y = max(0, min(new_y, screen_rect.height() - self.height()))
            self.move(new_x, new_y)
            self._drag_pos = event.globalPos()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """释放拖动"""
        self._drag_pos = None
        self.setCursor(Qt.ArrowCursor)
        super().mouseReleaseEvent(event)

    def eventFilter(self, obj, event):
        """事件过滤器 - 捕获子控件的鼠标事件用于拖动，不阻塞事件"""
        if event.type() == QEvent.MouseButtonPress:
            if event.button() == Qt.LeftButton and self._drag_pos is None:
                self._drag_pos = event.globalPos()
                self.setCursor(Qt.ClosedHandCursor)
        elif event.type() == QEvent.MouseMove:
            if self._drag_pos is not None:
                delta = event.globalPos() - self._drag_pos
                new_x = self.x() + delta.x()
                new_y = self.y() + delta.y()
                screen_rect = QApplication.desktop().screenGeometry()
                new_x = max(0, min(new_x, screen_rect.width() - self.width()))
                new_y = max(0, min(new_y, screen_rect.height() - self.height()))
                self.move(new_x, new_y)
                self._drag_pos = event.globalPos()
        elif event.type() == QEvent.MouseButtonRelease:
            if event.button() == Qt.LeftButton:
                self._drag_pos = None
                self.setCursor(Qt.ArrowCursor)
        return False  # 不阻塞事件，让按钮等子控件正常工作

    def init_ui(self):
        # 主布局
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(2)

        # 顶部按钮栏
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(5)

        # 最小化按钮 - 变成小按钮
        self.mini_btn = QPushButton("—")
        self.mini_btn.setFixedSize(20, 20)
        self.mini_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(200, 200, 200, 150);
                border: none;
                border-radius: 3px;
                color: #333;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: rgba(180, 180, 180, 200);
            }
        """)
        btn_layout.addWidget(self.mini_btn)
        # 为按钮安装事件过滤器，使其支持拖动
        self.mini_btn.installEventFilter(self)

        # 最大化按钮 - 切换回正常模式
        self.max_btn = QPushButton("□")
        self.max_btn.setFixedSize(20, 20)
        self.max_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(200, 200, 200, 150);
                border: none;
                border-radius: 3px;
                color: #333;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: rgba(180, 180, 180, 200);
            }
        """)
        btn_layout.addWidget(self.max_btn)
        # 为按钮安装事件过滤器，使其支持拖动
        self.max_btn.installEventFilter(self)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # 股票列表容器
        self.stock_container = QWidget()
        self.stock_layout = QVBoxLayout(self.stock_container)
        self.stock_layout.setContentsMargins(0, 0, 0, 0)
        self.stock_layout.setSpacing(1)
        layout.addWidget(self.stock_container)

        # 今日总盈亏标签
        self.total_profit_label = QLabel()
        self.total_profit_label.setAlignment(Qt.AlignCenter)
        self.total_profit_label.setStyleSheet("""
            QLabel {
                background-color: rgba(255, 255, 255, 120);
                padding: 3px 6px;
                border-radius: 3px;
                font-size: 13px;
                font-weight: bold;
                font-family: Microsoft YaHei;
                color: #333;
            }
        """)
        self.total_profit_label.setFixedHeight(24)
        self.total_profit_label.setText("今日盈亏: --")
        self.total_profit_label.installEventFilter(self)
        layout.addWidget(self.total_profit_label)

        layout.addStretch()

        # 设置整体样式 - 半透明背景
        self.setStyleSheet("""
            QWidget {
                background-color: rgba(240, 240, 240, 180);
            }
        """)
        self.setFixedWidth(220)  # 加宽以容纳三列（名称、涨幅、今日盈亏）
        self.setMinimumHeight(100)

    def update_stocks(self, stocks, stock_data, positions):
        """更新股票显示 - 显示当日股票实时涨幅和今日盈亏，按涨幅倒序排列"""
        # 清除旧的标签
        for label in self.stock_labels:
            label.setParent(None)
            label.deleteLater()
        self.stock_labels.clear()

        # 构建股票数据列表，使用统一的 compute_position_profit 计算
        stock_list = []
        for code, name in stocks:
            position = positions.get(code, {})
            stock_data_entry = stock_data.get(code, None)
            p = compute_position_profit(code, position, stock_data_entry)
            change_percent = p['change_percent']
            today_profit = p['today_profit']
            stock_list.append({
                'code': code,
                'name': name,
                'change_percent': change_percent,
                'today_profit': today_profit
            })

        # 按涨幅倒序排列（涨幅最大的在最前面）
        stock_list.sort(key=lambda x: x['change_percent'], reverse=True)

        # 计算今日总盈亏
        total_today_profit = sum(s['today_profit'] for s in stock_list)

        for stock_info in stock_list:
            code = stock_info['code']
            name = stock_info['name']
            change_percent = stock_info['change_percent']
            today_profit = stock_info['today_profit']

            if change_percent >= 0:
                color = "#d32f2f"
                percent_text = f"+{change_percent:.3f}%"
            else:
                color = "#388e3c"
                percent_text = f"{change_percent:.3f}%"

            # 格式化今日盈亏
            if today_profit >= 0:
                profit_color = "#d32f2f"
                profit_text = f"+{today_profit:,.0f}"
            else:
                profit_color = "#388e3c"
                profit_text = f"{today_profit:,.0f}"

            # 使用单个QLabel + HTML表格，显示名称、涨幅、今日盈亏
            label = QLabel()
            label.setTextFormat(Qt.RichText)
            label.setText(
                f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
                f'<tr>'
                f'<td align="left" style="color:{color};font-size:14px;font-family:Microsoft YaHei;">'
                f'{name}</td>'
                f'<td align="center" style="color:{color};font-size:14px;font-weight:bold;font-family:Microsoft YaHei;">'
                f'{percent_text}</td>'
                f'<td align="right" style="color:{profit_color};font-size:12px;font-family:Microsoft YaHei;">'
                f'{profit_text}</td>'
                f'</tr>'
                f'</table>'
            )
            label.setStyleSheet(f"""
                QLabel {{
                    background-color: rgba(255, 255, 255, 100);
                    padding: 2px 6px;
                    border-radius: 2px;
                }}
            """)
            label.setFixedHeight(22)
            # 为标签安装事件过滤器，使其支持拖动
            label.installEventFilter(self)

            self.stock_layout.addWidget(label)
            self.stock_labels.append(label)

        # 更新今日总盈亏标签
        if total_today_profit >= 0:
            profit_color = "#d32f2f"
            profit_text = f"+{total_today_profit:,.0f}"
        else:
            profit_color = "#388e3c"
            profit_text = f"{total_today_profit:,.0f}"

        self.total_profit_label.setText(f"今日盈亏: {profit_text}")
        self.total_profit_label.setStyleSheet(f"""
            QLabel {{
                background-color: rgba(255, 255, 255, 120);
                padding: 3px 6px;
                border-radius: 3px;
                font-size: 13px;
                font-weight: bold;
                font-family: Microsoft YaHei;
                color: {profit_color};
            }}
        """)

    def set_mini_button_callback(self, callback):
        """设置最小化按钮回调"""
        self.mini_btn.clicked.connect(callback)

    def set_max_button_callback(self, callback):
        """设置最大化按钮回调"""
        self.max_btn.clicked.connect(callback)


class MiniFloatButton(QWidget):
    """迷你浮动按钮 - 极简模式最小化后的状态"""
    def __init__(self, parent=None):
        super().__init__(parent)
        # 必须作为独立窗口，不能有parent
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_QuitOnClose, False)  # 关闭时不退出程序
        self.init_ui()

    def init_ui(self):
        # 创建按钮
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.btn = QPushButton("股")
        self.btn.setFixedSize(30, 30)
        self.btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(100, 100, 100, 150);
                border: none;
                border-radius: 15px;
                color: white;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(80, 80, 80, 200);
            }
        """)
        layout.addWidget(self.btn)

        self.setFixedSize(30, 30)

    def set_click_callback(self, callback):
        """设置点击回调"""
        self.btn.clicked.connect(callback)


class NumericTableWidgetItem(QTableWidgetItem):
    """支持数值排序的表格项"""
    def __lt__(self, other):
        try:
            if other is None:
                return False
            self_data = self.data(Qt.UserRole)
            other_data = other.data(Qt.UserRole)
            if self_data is not None and other_data is not None:
                if isinstance(self_data, (int, float)) and isinstance(other_data, (int, float)):
                    return self_data < other_data
                # 都是字符串时按字符串比较
                if isinstance(self_data, str) and isinstance(other_data, str):
                    return self_data < other_data
            # 回退到文本比较
            return self.text() < other.text()
        except Exception:
            return False


class ExcelStyleTable(QTableWidget):
    """Excel样式表格 - 合并单元格显示风格"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()
    
    def init_ui(self):
        # 设置8列表格 - 名称/代码→分时→涨跌幅→其他
        self.setColumnCount(8)
        self.setHorizontalHeaderLabels([
            '名称/代码', '分时', '涨跌幅', '盈亏/盈亏%', '当日盈亏/当日%', '成本/现价', '持有数', '市值'
        ])

        # 设置样式
        self.setStyleSheet("""
            QTableWidget {
                gridline-color: #e0e0e0;
                background-color: white;
                alternate-background-color: #fafafa;
            }
            QTableWidget::item {
                padding: 4px;
                border: none;
            }
            QHeaderView::section {
                background-color: #f5f5f5;
                border: none;
                border-bottom: 1px solid #e0e0e0;
                padding: 6px 4px;
                font-weight: bold;
                color: #333;
                font-size: 12px;
            }
            QTableWidget::item:selected {
                background-color: #fff3cd;
                color: #333;
            }
        """)

        # 设置表头 - 自适应宽度，启用排序
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        # 第1列（迷你分时图）固定宽度
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.resizeSection(1, 80)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.sortIndicatorChanged.connect(self._on_sort_indicator_changed)
        self._sort_column = -1
        self._sort_order = Qt.AscendingOrder

        # 设置行高（需要容纳两行文字，增加到70px确保名称和代码完整显示）
        self.verticalHeader().setDefaultSectionSize(70)
        self.verticalHeader().setVisible(False)

        # 启用交替行颜色
        self.setAlternatingRowColors(True)

        # 禁止编辑
        self.setEditTriggers(QTableWidget.NoEditTriggers)

        # 设置选择模式 - 支持多选
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setSelectionMode(QTableWidget.ExtendedSelection)

        # 双击信号
        self.cellDoubleClicked.connect(self.on_cell_double_clicked)

        # 存储迷你图widget引用
        self._mini_charts = {}
    
    def _on_sort_indicator_changed(self, column, order):
        """自定义排序 - 延迟刷新避免在信号回调中销毁widget导致崩溃"""
        # 断开信号避免递归
        try:
            self.horizontalHeader().sortIndicatorChanged.disconnect(self._on_sort_indicator_changed)
        except Exception:
            pass
        self._sort_column = column
        self._sort_order = order
        # 用QTimer延迟到下一轮事件循环，避免在信号回调中操作表格导致崩溃
        main_window = self.window()
        if hasattr(main_window, 'refresh_table'):
            QTimer.singleShot(0, main_window.refresh_table)
        # 重新连接信号
        self.horizontalHeader().sortIndicatorChanged.connect(self._on_sort_indicator_changed)
    
    def on_cell_double_clicked(self, row, column):
        """处理双击事件"""
        # 获取主窗口（StealthStockMonitor）
        main_window = self.window()

        # 第0列（股票名称列）双击 - 弹出买卖对话框
        if column == 0:
            first_col_text = self.item(row, 0).text()
            parts = first_col_text.split('\n')
            if len(parts) >= 2:
                code = parts[1]  # 第二行是代码
                name = parts[0]  # 第一行是名称
                if code and name and hasattr(main_window, 'show_trade_dialog'):
                    main_window.show_trade_dialog(code, name)
            return

        # 第5列是成本/现价，第6列是持有数 - 允许编辑
        if column == 5 or column == 6:
            # 从第一列提取代码
            first_col_text = self.item(row, 0).text()
            parts = first_col_text.split('\n')
            if len(parts) >= 2:
                code = parts[1]  # 第二行是代码
                name = parts[0]  # 第一行是名称

                if hasattr(main_window, 'edit_position_data'):
                    # 获取当前值
                    if column == 6:  # 持有数列
                        current_value = main_window.positions.get(code, {}).get('quantity', 0)
                        main_window.edit_position_data(code, name, 'quantity', current_value)
                    elif column == 5:  # 成本列
                        current_value = main_window.positions.get(code, {}).get('cost_price', 0)
                        main_window.edit_position_data(code, name, 'cost_price', current_value)
            return

        # 第1列（迷你分时图列）双击 - 弹出全屏分时图
        if column == 1:
            first_col_text = self.item(row, 0).text()
            parts = first_col_text.split('\n')
            if len(parts) >= 2:
                code = parts[1]
                name = parts[0]
                if code and name and hasattr(main_window, 'on_stock_double_clicked'):
                    # 延迟到下一轮事件循环执行，避免在信号回调中操作UI导致崩溃
                    QTimer.singleShot(0, lambda c=code, n=name, mw=main_window: mw.on_stock_double_clicked(c, n))
            return

        # 其他列双击 - 切换到走势图
        if hasattr(main_window, 'on_stock_double_clicked'):
            # 从第一列提取代码（格式：名称\n代码）
            first_col_text = self.item(row, 0).text()
            parts = first_col_text.split('\n')
            if len(parts) >= 2:
                code = parts[1]  # 第二行是代码
                name = parts[0]  # 第一行是名称

                if code and name:
                    # 延迟到下一轮事件循环执行，避免在信号回调中操作UI导致崩溃
                    QTimer.singleShot(0, lambda c=code, n=name, mw=main_window: mw.on_stock_double_clicked(c, n))
    
    def keyPressEvent(self, event):
        """处理键盘事件 - 支持Delete键删除"""
        from PyQt5.QtCore import Qt
        
        if event.key() == Qt.Key_Delete:
            # 获取主窗口并调用移除功能
            main_window = self.window()
            if hasattr(main_window, 'remove_selected_stock'):
                main_window.remove_selected_stock()
        else:
            # 其他按键交给父类处理
            super().keyPressEvent(event)


class MiniChartWidget(QWidget):
    """迷你分时图控件 - 可嵌入表格单元格，支持点击弹出全屏"""
    clicked = pyqtSignal(str, str)  # (code, name)

    def __init__(self, code="", name="", parent=None):
        super().__init__(parent)
        self.code = code
        self.name = name
        self.prices = []      # 分时价格序列
        self.prev_close = 0
        self.setFixedHeight(60)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("点击查看分时图")

    def set_data(self, prices, prev_close):
        """设置分时价格数据"""
        self.prices = prices if prices else []
        self.prev_close = prev_close
        self.update()

    def mousePressEvent(self, event):
        """点击弹出全屏分时图"""
        if event.button() == Qt.LeftButton and self.code:
            self.clicked.emit(self.code, self.name)
        super().mousePressEvent(event)

    def paintEvent(self, event):
        """绘制迷你分时图 - 以昨收价为中心对称范围，与主分时图一致"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return

        # 背景
        painter.fillRect(self.rect(), QColor('#fafafa'))

        if not self.prices or len(self.prices) < 2:
            # 无数据时显示横线
            painter.setPen(QPen(QColor('#ddd'), 1))
            painter.drawLine(4, h // 2, w - 4, h // 2)
            return

        # 价格范围：以昨收价为中心，对称扩展（与主分时图一致）
        real_min, real_max = min(self.prices), max(self.prices)
        if self.prev_close > 0:
            up_dev = abs(real_max - self.prev_close)
            down_dev = abs(self.prev_close - real_min)
            display_range = max(up_dev, down_dev) * 1.1
            if display_range < abs(self.prev_close) * 0.005:
                display_range = abs(self.prev_close) * 0.01
            min_p = self.prev_close - display_range
            max_p = self.prev_close + display_range
        else:
            min_p, max_p = real_min, real_max
        rng = max_p - min_p if max_p > min_p else 0.01

        # 涨跌判断
        last_price = self.prices[-1]
        is_up = last_price >= self.prev_close if self.prev_close > 0 else True

        # 颜色
        if is_up:
            line_color = QColor('#e53935')
            fill_color = QColor(229, 57, 53, 30)
        else:
            line_color = QColor('#43a047')
            fill_color = QColor(67, 160, 71, 30)

        pad = 3
        chart_w = w - pad * 2
        chart_h = h - pad * 2

        # 昨收价参考线位置（中线）
        if self.prev_close > 0:
            prev_y = (pad + chart_h) - ((self.prev_close - min_p) / rng) * chart_h
            painter.setPen(QPen(QColor('#ddd'), 1, Qt.DashLine))
            painter.drawLine(pad, int(prev_y), pad + chart_w, int(prev_y))

        # 计算坐标
        points = []
        for i, price in enumerate(self.prices):
            x = pad + (i / max(len(self.prices) - 1, 1)) * chart_w
            y = (pad + chart_h) - ((price - min_p) / rng) * chart_h
            points.append((x, y))

        # 填充区域（到图表底部）
        fill_path = QPainterPath()
        fill_path.moveTo(points[0][0], pad + chart_h)
        for px, py in points:
            fill_path.lineTo(px, py)
        fill_path.lineTo(points[-1][0], pad + chart_h)
        fill_path.closeSubpath()
        painter.fillPath(fill_path, fill_color)

        # 绘制价格线
        line_path = QPainterPath()
        for i, (px, py) in enumerate(points):
            if i == 0:
                line_path.moveTo(px, py)
            else:
                line_path.lineTo(px, py)
        painter.setPen(QPen(line_color, 1.5))
        painter.drawPath(line_path)

        # 最后一个点加小圆点
        lx, ly = points[-1]
        painter.setPen(QPen(line_color, 1.5))
        painter.setBrush(QBrush(line_color))
        painter.drawEllipse(QPointF(lx, ly), 2.5, 2.5)



class StockInfoFetcher:
    """股票信息获取工具类"""
    @staticmethod
    def get_stock_name(code):
        """根据股票代码获取股票名称"""
        try:
            # 判断市场前缀
            if code.startswith('HK') or code.startswith('hk'):
                # 港股 - 腾讯API需要5位数字，不足补零
                prefix = 'hk'
                query_code = code[2:].zfill(5)
            elif code.startswith('5') or code.startswith('6'):
                # 上海（含ETF）
                prefix = 'sh'
                query_code = code
            else:
                # 深圳
                prefix = 'sz'
                query_code = code

            url = f'https://qt.gtimg.cn/q={prefix}{query_code}'

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://finance.qq.com'
            }

            response = requests.get(url, headers=headers, timeout=10)
            response.encoding = 'gbk'

            # 解析返回数据 - 格式如: v_sh600519="1~贵州茅台~600519~..."
            if '=' in response.text:
                data_part = response.text.split('=')[1].strip('";').split('~')
                if len(data_part) > 1 and data_part[1]:
                    return data_part[1]
        except Exception as e:
            print(f"获取股票名称失败: {e}")

        return None


class BrokerageAPI:
    """券商API接口基类（抽象类）"""
    def login(self, username, password):
        """登录券商系统"""
        raise NotImplementedError
    
    def get_positions(self):
        """获取持仓数据"""
        raise NotImplementedError
    
    def get_account_info(self):
        """获取账户信息"""
        raise NotImplementedError


class GTJA_Brokerage(BrokerageAPI):
    """国泰君安证券API示例实现
    
    注意：这需要国泰君安开放API接口或使用其官方交易软件SDK
    此处仅提供框架示例，实际使用需要：
    1. 联系国泰君安申请API访问权限
    2. 获取API密钥和文档
    3. 安装相应的SDK包
    """
    
    def __init__(self):
        self.api_key = None
        self.session = None
        self.logged_in = False
    
    def login(self, username, password):
        """登录国泰君安交易系统
        
        实际实现需要：
        - 使用国泰君安提供的API endpoint
        - 可能需要RSA加密密码
        - 获取session token
        """
        # 示例代码框架
        try:
            # 这里应该调用真实的国泰君安API
            # response = requests.post('https://api.gtja.com/login', {...})
            # self.session = response.json()['token']
            # self.logged_in = True
            pass
        except Exception as e:
            print(f"国泰君安登录失败: {e}")
            return False
        return True
    
    def get_positions(self):
        """获取持仓数据
        
        返回格式示例：
        [
            {
                'code': '600519',
                'name': '贵州茅台',
                'quantity': 100,
                'cost_price': 1800.00,
                'current_price': 1850.00,
                'market_value': 185000.00,
                'profit_loss': 5000.00
            },
            ...
        ]
        """
        if not self.logged_in:
            return []
        
        try:
            # 这里应该调用真实的国泰君安API获取持仓
            # response = requests.get('https://api.gtja.com/positions', 
            #                        headers={'Authorization': f'Bearer {self.session}'})
            # return response.json()
            pass
        except Exception as e:
            print(f"获取持仓失败: {e}")
        
        return []
    
    def get_account_info(self):
        """获取账户资金信息"""
        if not self.logged_in:
            return {}
        
        try:
            # 这里应该调用真实的国泰君安API获取账户信息
            pass
        except Exception as e:
            print(f"获取账户信息失败: {e}")
        
        return {}


class PositionManager:
    """持仓数据管理器"""
    def __init__(self):
        self.brokerage = None
        self.positions = []
        self.last_update = None
    
    def connect_brokerage(self, brokerage_type='gtja'):
        """连接券商API"""
        if brokerage_type == 'gtja':
            self.brokerage = GTJA_Brokerage()
        # 可以添加其他券商支持
        # elif brokerage_type == 'htsc':
        #     self.brokerage = HTSC_Brokerage()
        return self.brokerage is not None
    
    def login(self, username, password):
        """登录券商系统"""
        if not self.brokerage:
            return False
        return self.brokerage.login(username, password)
    
    def fetch_positions(self):
        """获取并更新持仓数据"""
        if not self.brokerage:
            return []
        
        self.positions = self.brokerage.get_positions()
        self.last_update = datetime.now()
        return self.positions
    
    def get_position_stocks(self):
        """获取持仓股票代码列表"""
        return [(p['code'], p['name']) for p in self.positions if 'code' in p]


class StealthStockMonitor(QMainWindow):
    """主窗口"""
    def __init__(self):
        super().__init__()
        self.stocks = []
        self.stock_data = {}  # 存储股票数据
        self._minute_data_cache = {}  # 分时数据缓存 {code: minute_data}
        self._minute_data_fetching = set()  # 正在获取分时数据的股票code集合
        self._minute_data_cache_date = datetime.now().strftime('%Y%m%d')  # 缓存日期，用于判断是否需要刷新
        # 分时图定时刷新器 - 每60秒更新一次
        self._minute_refresh_timer = QTimer(self)
        self._minute_refresh_timer.timeout.connect(self._refresh_all_minute_charts)
        self._minute_refresh_timer.start(60 * 1000)  # 1分钟
        self.data_fetcher = None
        self.current_chart_stock = None  # 当前在走势图中显示的股票代码
        self.position_manager = PositionManager()  # 持仓管理器
        # 极简模式相关
        self.minimalist_panel = None  # 极简模式面板
        self.mini_float_button = None  # 迷你浮动按钮
        self.is_minimalist_mode = False  # 是否处于极简模式
        self.last_import_file = ""  # 上次导入的文件路径
        self.load_config()  # 加载配置（包括上次导入路径）
        self.load_stocks()
        self.init_ui()
        self.start_data_fetcher()
    
    def load_config(self):
        """加载配置文件（包括上次导入的文件路径）"""
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
                self.last_import_file = config.get('last_import_file', '')
        except:
            self.last_import_file = ""
    
    def save_config(self):
        """保存配置文件（包括上次导入的文件路径）"""
        config = {
            'last_import_file': self.last_import_file
        }
        with open('config.json', 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    
    def load_stocks(self):
        """加载监控股票列表"""
        # 默认股票 - 使用图片中的真实持仓数据
        default_stocks = [
            ('688012', '中微公司'),
            ('300454', '深信服'),
            ('002920', '德赛西威'),
            ('600221', '海航控股'),
            ('300750', '宁德时代'),
            ('688126', '沪硅产业'),
            ('688181', '八亿时空'),
            ('688008', '澜起科技'),
            ('300604', '长川科技'),
            ('002475', '立讯精密'),
            ('300308', '中际旭创'),
        ]
        
        # 尝试从文件加载
        try:
            with open('stocks.json', 'r', encoding='utf-8') as f:
                self.stocks = json.load(f)
        except:
            self.stocks = default_stocks
        
        # 加载持仓数据（包含成本价、数量等）
        self.load_positions()
        
        # 启动时数据一致性检查与自动修复
        self._fix_data_consistency()
    
    def _fix_data_consistency(self):
        """启动时检查并修复数据一致性"""
        # 1. 清除positions中不在stocks里的股票（防止删除后残留数据）
        stock_codes = set(code for code, _ in self.stocks)
        extra_codes = [code for code in self.positions.keys() if code not in stock_codes]
        if extra_codes:
            for code in extra_codes:
                del self.positions[code]
            self.save_positions()
        
        # 2. 清除stocks中不在positions里且没有持仓信息的股票（可选）
        # 注意：这里不自动清除stocks，因为用户可能只是在监视而未持仓
        
        # 3. 新交易日自动清理 is_sold=True 的股票
        # 检查是否是新的一天
        import os
        try:
            config_mtime = os.path.getmtime('config.json') if os.path.exists('config.json') else 0
            last_trading_date_str = ''
            # 从positions中推断（简化处理：直接检查文件修改时间是否跨天）
            from datetime import datetime, timedelta
            today_str = datetime.now().strftime('%Y-%m-%d')
            # 检查config中保存的最后交易日标记
            need_clean_sold = False
            try:
                with open('config.json', 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                    last_trading_date_str = cfg.get('_last_trading_date', '')
                if last_trading_date_str != today_str:
                    need_clean_sold = True
            except:
                need_clean_sold = True  # 无法判断时默认检查
            
            if need_clean_sold:
                # 清除 is_sold=True 的股票
                sold_codes = [code for code, info in self.positions.items() 
                              if info.get('is_sold', False)]
                if sold_codes:
                    for code in sold_codes:
                        self.stocks = [(c, n) for c, n in self.stocks if c != code]
                        del self.positions[code]
                    self.save_stocks()
                    self.save_positions()
                
                # 保存本次交易日标记
                try:
                    with open('config.json', 'r', encoding='utf-8') as f:
                        cfg = json.load(f)
                except:
                    cfg = {}
                cfg['_last_trading_date'] = today_str
                with open('config.json', 'w', encoding='utf-8') as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
        except:
            pass  # 简化处理，不影响启动
    
    def load_positions(self):
        """加载持仓详细信息"""
        try:
            with open('positions.json', 'r', encoding='utf-8') as f:
                self.positions = json.load(f)
        except:
            # 根据图片中的数据初始化持仓
            self.positions = {
                '688012': {'name': '中微公司', 'quantity': 200, 'cost_price': 277.088, 'change_percent': 0},
                '300454': {'name': '深信服', 'quantity': 100, 'cost_price': 509.731, 'change_percent': 0},
                '002920': {'name': '德赛西威', 'quantity': 400, 'cost_price': 105.313, 'change_percent': 0},
                '600221': {'name': '海航控股', 'quantity': 40000, 'cost_price': 1.302, 'change_percent': 0},
                '300750': {'name': '宁德时代', 'quantity': 200, 'cost_price': 407.765, 'change_percent': 0},
                '688126': {'name': '沪硅产业', 'quantity': 2000, 'cost_price': 30.974, 'change_percent': 0},
                '688181': {'name': '八亿时空', 'quantity': 300, 'cost_price': 228.090, 'change_percent': 0},
                '688008': {'name': '澜起科技', 'quantity': 200, 'cost_price': 174.077, 'change_percent': 0},
                '300604': {'name': '长川科技', 'quantity': 600, 'cost_price': 82.791, 'change_percent': 0},
                '002475': {'name': '立讯精密', 'quantity': 1100, 'cost_price': 69.592, 'change_percent': 0},
                '300308': {'name': '中际旭创', 'quantity': 300, 'cost_price': 370.517, 'change_percent': 0},
            }
            self.save_positions()
    
    def save_positions(self):
        """保存持仓数据"""
        with open('positions.json', 'w', encoding='utf-8') as f:
            json.dump(self.positions, f, ensure_ascii=False, indent=2)
    
    def save_stocks(self):
        """保存监控股票列表"""
        with open('stocks.json', 'w', encoding='utf-8') as f:
            json.dump(self.stocks, f, ensure_ascii=False)
    
    def init_ui(self):
        """初始化界面"""
        # 窗口基本设置 - 伪装成Excel
        self.setWindowTitle("Book1 - Excel")  # 伪装的标题
        self.setGeometry(100, 100, 850, 500)
        
        # 设置为普通窗口（修复托盘问题）
        self.setWindowFlags(Qt.Window | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint | Qt.WindowCloseButtonHint)
        
        # 设置样式 - Excel风格
        self.setStyleSheet("""
            QMainWindow {
                background-color: white;
            }
            QLabel {
                color: #333;
            }
            QPushButton {
                background-color: #f0f0f0;
                border: 1px solid #ccc;
                border-radius: 2px;
                padding: 4px 10px;
                font-size: 13px;
                min-width: 60px;
            }
            QPushButton:hover {
                background-color: #e0e0e0;
                border-color: #999;
            }
            QLineEdit {
                border: 1px solid #ccc;
                border-radius: 2px;
                padding: 3px 6px;
                font-size: 11px;
            }
            QTabWidget::pane {
                border: 1px solid #d0d0d0;
            }
            QTabBar::tab {
                background: #f0f0f0;
                border: 1px solid #d0d0d0;
                padding: 4px 12px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background: white;
                border-bottom-color: white;
            }
        """)
        
        # 主布局
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # 工具栏（模仿Excel）
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setContentsMargins(5, 5, 5, 5)
        
        # 买入按钮
        buy_btn = QPushButton("买入")
        buy_btn.clicked.connect(self.show_buy_dialog)
        toolbar_layout.addWidget(buy_btn)
        
        # 卖出按钮
        sell_btn = QPushButton("卖出")
        sell_btn.clicked.connect(self.show_sell_dialog)
        toolbar_layout.addWidget(sell_btn)
        
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.manual_refresh)
        toolbar_layout.addWidget(refresh_btn)
        
        import_btn = QPushButton("导入")
        import_btn.clicked.connect(self.import_from_csv)
        toolbar_layout.addWidget(import_btn)
        
        sync_btn = QPushButton("同步")
        sync_btn.clicked.connect(self.sync_from_last_import)
        toolbar_layout.addWidget(sync_btn)
        
        remove_btn = QPushButton("移除")
        remove_btn.clicked.connect(self.remove_selected_stock)
        toolbar_layout.addWidget(remove_btn)

        # 极简模式按钮
        minimalist_btn = QPushButton("极简")
        minimalist_btn.clicked.connect(self.toggle_minimalist_mode)
        toolbar_layout.addWidget(minimalist_btn)

        toolbar_layout.addStretch()
        
        # 状态显示
        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet("color: #333; font-size: 13px; font-weight: bold;")
        toolbar_layout.addWidget(self.status_label)
        
        main_layout.addLayout(toolbar_layout)
        
        # 标签页
        self.tab_widget = QTabWidget()
        
        # 数据表格标签页
        table_tab = QWidget()
        table_layout = QVBoxLayout(table_tab)
        table_layout.setContentsMargins(0, 0, 0, 0)
        
        self.excel_table = ExcelStyleTable(table_tab)  # 传递parent
        table_layout.addWidget(self.excel_table)
        
        self.tab_widget.addTab(table_tab, "数据监控")
        
        # 走势图标签页
        chart_tab = QWidget()
        chart_layout = QVBoxLayout(chart_tab)
        chart_layout.setContentsMargins(5, 5, 5, 5)
        
        self.chart_widget = StockChartWidget('')
        chart_layout.addWidget(self.chart_widget)
        
        self.tab_widget.addTab(chart_tab, "走势分析")
        
        main_layout.addWidget(self.tab_widget)
        
        # 底部状态栏
        status_bar_widget = QWidget()
        status_bar_widget.setStyleSheet("background-color: #f0f0f0; border-top: 1px solid #d0d0d0;")
        status_bar_widget.setMinimumHeight(36)
        status_bar_layout = QHBoxLayout(status_bar_widget)
        status_bar_layout.setContentsMargins(10, 6, 10, 6)
        status_bar_layout.setSpacing(15)

        self.market_status_label = QLabel("● 交易中")
        self.market_status_label.setStyleSheet("color: #4caf50; font-size: 13px;")
        status_bar_layout.addWidget(self.market_status_label)

        self.last_update_label = QLabel("最后更新: --:--:--")
        self.last_update_label.setStyleSheet("color: #666; font-size: 13px;")
        status_bar_layout.addWidget(self.last_update_label)

        status_bar_layout.addStretch()

        self.stock_count_label = QLabel(f"共 {len(self.stocks)} 只股票")
        self.stock_count_label.setStyleSheet("color: #666; font-size: 13px;")
        status_bar_layout.addWidget(self.stock_count_label)

        main_layout.addWidget(status_bar_widget)
        
        # 创建系统托盘
        self.create_system_tray()
        
        # 初始化表格
        self.refresh_table()
    
    def _sort_stocks(self, stocks, column, order):
        """根据指定列对股票列表排序，返回排序后的新列表"""
        def get_sort_key(stock_tuple):
            code, name = stock_tuple[0], stock_tuple[1]
            p = self.positions.get(code, {})
            real = self.stock_data.get(code, {})
            current_price = real.get('current_price', 0) if real else 0
            change_percent = real.get('change_percent', 0) if real else 0

            if column == 0:
                # 名称/代码列 - 按代码排序
                return code
            elif column == 1:
                # 分时图列 - 按涨跌幅排序
                return change_percent
            elif column == 2:
                # 涨跌幅
                return change_percent
            elif column == 3:
                # 盈亏
                return p.get('profit_loss', 0)
            elif column == 4:
                # 当日盈亏
                return p.get('today_profit', 0)
            elif column == 5:
                # 成本/现价 - 按现价
                return current_price
            elif column == 6:
                # 持有数
                return p.get('quantity', 0)
            elif column == 7:
                # 市值
                return p.get('market_value', 0)
            return 0

        try:
            reverse = (order == Qt.DescendingOrder)
            # 尝试数值排序，字符串列单独处理
            if column == 0:
                return sorted(stocks, key=get_sort_key, reverse=reverse)
            else:
                return sorted(stocks, key=lambda s: get_sort_key(s) or 0, reverse=reverse)
        except Exception:
            return stocks

    def refresh_table(self):
        """刷新表格数据 - 使用统一的 compute_position_profit 计算盈亏"""
        try:
            # 保存当前排序状态
            sort_col = self.excel_table._sort_column
            sort_order = self.excel_table._sort_order

            # 按排序状态对self.stocks排序（排序后重建表格，避免sortItems与cellWidget冲突）
            if sort_col >= 0 and len(self.stocks) > 1:
                self.stocks = self._sort_stocks(self.stocks, sort_col, sort_order)

            # 先清空widget缓存，再setRowCount(0)彻底清理表格，避免widget状态混乱
            self.excel_table._mini_charts.clear()
            self.excel_table.setRowCount(0)
            self.excel_table.setRowCount(len(self.stocks))

            total_market_value = 0  # 总市值
            total_profit_loss = 0   # 总盈亏
            total_today_profit = 0  # 当日总盈亏
            pending_fetch = []  # 待获取分时数据的股票列表（串行获取，避免并发崩溃）

            for row, (code, name) in enumerate(self.stocks):
                position = self.positions.get(code, {})
                stock_data_entry = self.stock_data.get(code, None)

                # 使用统一的计算函数
                p = compute_position_profit(code, position, stock_data_entry)

                quantity = p['quantity']
                cost_price = p['cost_price']
                current_price = p['current_price']
                market_value = p['market_value']
                profit_loss = p['total_profit']
                profit_pct = p['total_profit_percent']
                today_profit = p['today_profit']
                today_pct = p['today_profit_percent']
                change_percent = p['change_percent']

                # 同步保存最新的关键字段（用于下一次刷新 / 极简模式读取）
                if code in self.positions and not p['is_sold']:
                    if stock_data_entry is not None:
                        rt_price = stock_data_entry.get('current_price', 0)
                        rt_change = stock_data_entry.get('change', 0)
                        if rt_price > 0 and rt_change != 0:
                            self.positions[code]['prev_close'] = rt_price - rt_change
                    self.positions[code]['current_price'] = current_price
                    self.positions[code]['market_value'] = market_value
                    self.positions[code]['total_profit'] = profit_loss
                    self.positions[code]['total_profit_percent'] = profit_pct
                    self.positions[code]['today_profit'] = today_profit
                    self.positions[code]['today_profit_percent'] = today_pct
                    self.positions[code]['change_percent'] = change_percent

                # 累加总计（市值只累加未卖出的，盈亏全部累加）
                if not p['is_sold']:
                    total_market_value += market_value
                total_profit_loss += profit_loss
                total_today_profit += today_profit
                
                # 第0列：名称/代码（两行显示）
                name_code_text = f"{name}\n{code}"
                nc_item = NumericTableWidgetItem(name_code_text)
                nc_item.setData(Qt.UserRole, code)  # 用代码作为排序依据
                nc_item.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
                nc_item.setTextAlignment(Qt.AlignLeft | Qt.AlignTop)
                self.excel_table.setItem(row, 0, nc_item)
                
                # 第1列：迷你分时图
                try:
                    if code in self.excel_table._mini_charts:
                        mini_chart = self.excel_table._mini_charts[code]
                    else:
                        mini_chart = MiniChartWidget(code, name)
                        mini_chart.clicked.connect(
                            lambda c, n, mc=mini_chart: QTimer.singleShot(0, lambda: self.on_stock_double_clicked(c, n))
                        )
                        self.excel_table._mini_charts[code] = mini_chart
                    self.excel_table.setCellWidget(row, 1, mini_chart)
                    # 设置占位item，避免排序时None崩溃
                    placeholder = NumericTableWidgetItem("")
                    placeholder.setData(Qt.UserRole, 0)
                    self.excel_table.setItem(row, 1, placeholder)
                    # 检查是否需要刷新分时数据缓存（新的一天）
                    today = datetime.now().strftime('%Y%m%d')
                    if self._minute_data_cache_date != today:
                        self._minute_data_cache = {}
                        self._minute_data_cache_date = today
                    # 使用缓存的分时数据，避免每次刷新都发起HTTP请求
                    minute_data = self._minute_data_cache.get(code)
                    if minute_data is None:
                        # 首次或缓存失效时，收集到待获取列表，稍后串行获取（避免并发崩溃）
                        pending_fetch.append((code, name, mini_chart))
                    else:
                        prices = [d['price'] for d in minute_data]
                        prev_close = minute_data[0].get('prev_close', prices[0] if prices else 0)
                        mini_chart.set_data(prices, prev_close)
                except Exception as e:
                    pass  # 单个股票widget创建失败不影响整体刷新
                
                # 第2列：涨跌幅（当日实时涨幅）
                if change_percent != 0:
                    chg_text = f"{change_percent:+.3f}%"
                    chg_item = NumericTableWidgetItem(chg_text)
                    chg_item.setFont(QFont("Consolas", 10, QFont.Bold))
                    chg_color = QColor('#d32f2f') if change_percent >= 0 else QColor('#388e3c')
                    chg_item.setForeground(chg_color)
                    chg_item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
                    chg_item.setData(Qt.UserRole, change_percent)
                else:
                    chg_item = NumericTableWidgetItem("--")
                    chg_item.setData(Qt.UserRole, 0)
                    chg_item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
                self.excel_table.setItem(row, 2, chg_item)
                
                # 第3列：盈亏/盈亏%（两行显示）- 只有有持仓数据才显示
                if profit_loss != 0 or profit_pct != 0:
                    profit_line1 = f"{profit_loss:+,.2f}"
                    profit_line2 = f"{profit_pct:+.3f}%"
                    profit_text = f"{profit_line1}\n{profit_line2}"
                    pl_item = NumericTableWidgetItem(profit_text)
                    pl_item.setFont(QFont("Consolas", 9, QFont.Bold))
                    pl_color = QColor('#d32f2f') if profit_loss >= 0 else QColor('#388e3c')
                    pl_item.setForeground(pl_color)
                    pl_item.setTextAlignment(Qt.AlignRight | Qt.AlignTop)
                    pl_item.setData(Qt.UserRole, profit_loss)
                else:
                    pl_item = NumericTableWidgetItem("--")
                    pl_item.setData(Qt.UserRole, 0)
                self.excel_table.setItem(row, 3, pl_item)
                
                # 第4列：当日盈亏/当日%（两行显示）- 只有有持仓数据才显示
                if today_profit != 0 or today_pct != 0:
                    today_line1 = f"{today_profit:+,.2f}"
                    today_line2 = f"{today_pct:+.3f}%"
                    today_text = f"{today_line1}\n{today_line2}"
                    tp_item = NumericTableWidgetItem(today_text)
                    tp_item.setFont(QFont("Consolas", 9, QFont.Bold))
                    tp_color = QColor('#d32f2f') if today_profit >= 0 else QColor('#388e3c')
                    tp_item.setForeground(tp_color)
                    tp_item.setTextAlignment(Qt.AlignRight | Qt.AlignTop)
                    tp_item.setData(Qt.UserRole, today_profit)
                else:
                    tp_item = NumericTableWidgetItem("--")
                    tp_item.setData(Qt.UserRole, 0)
                self.excel_table.setItem(row, 4, tp_item)
                
                # 第5列：成本/现价（两行显示）- 现价必须显示
                if current_price > 0:
                    if cost_price > 0:
                        # 有成本和现价
                        cost_line1 = f"成本:{cost_price:.3f}"
                        cost_line2 = f"现价:{current_price:.3f}"
                        cost_text = f"{cost_line1}\n{cost_line2}"
                    else:
                        # 只有现价
                        cost_line1 = "成本:--"
                        cost_line2 = f"现价:{current_price:.3f}"
                        cost_text = f"{cost_line1}\n{cost_line2}"
                    
                    cc_item = QTableWidgetItem(cost_text)
                    cc_item.setFont(QFont("Consolas", 9))
                    cc_item.setTextAlignment(Qt.AlignLeft | Qt.AlignTop)
                    self.excel_table.setItem(row, 5, cc_item)
                else:
                    self.excel_table.setItem(row, 5, QTableWidgetItem("--"))
                
                # 第6列：持有数
                if quantity > 0:
                    qty_item = NumericTableWidgetItem(f"{quantity:,}")
                    qty_item.setFont(QFont("Consolas", 9))
                    qty_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    qty_item.setData(Qt.UserRole, quantity)
                else:
                    qty_item = NumericTableWidgetItem("--")
                    qty_item.setData(Qt.UserRole, 0)
                self.excel_table.setItem(row, 6, qty_item)
                
                # 第7列：市值（最后一列）- 使用预存或计算的市值
                if market_value > 0:
                    mv_item = NumericTableWidgetItem(f"{market_value:,.2f}")
                    mv_item.setFont(QFont("Consolas", 10, QFont.Bold))
                    mv_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    mv_item.setData(Qt.UserRole, market_value)
                elif quantity > 0 and current_price > 0:
                    # 如果有数量和现价但没有预存市值，动态计算
                    calculated_mv = quantity * current_price
                    mv_item = NumericTableWidgetItem(f"{calculated_mv:,.2f}")
                    mv_item.setFont(QFont("Consolas", 10, QFont.Bold))
                    mv_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    mv_item.setData(Qt.UserRole, calculated_mv)
                else:
                    mv_item = NumericTableWidgetItem("--")
                    mv_item.setData(Qt.UserRole, 0)
                self.excel_table.setItem(row, 7, mv_item)
            
            # 排序已在填充前完成，无需再调用sortItems

            # 更新底部状态栏显示总盈亏
            self.update_total_summary(total_market_value, total_profit_loss, total_today_profit)

            # 串行获取分时数据（用一个后台线程依次获取，避免并发HTTP请求导致崩溃）
            if pending_fetch:
                self._fetch_minute_data_batch(pending_fetch)
        except Exception as e:
            pass  # 刷新失败不崩溃
    
    def update_total_summary(self, market_value, profit_loss, today_profit):
        """更新底部汇总信息"""
        summary_text = f"总市值: {market_value:,.0f} | 总盈亏: {profit_loss:+,.0f} | 今日盈亏: {today_profit:+,.0f}"
        self.status_label.setText(summary_text)
        self.status_label.setStyleSheet("color: #333; font-size: 13px; font-weight: bold;")
    
    def start_data_fetcher(self):
        """启动数据获取器"""
        codes = [code for code, _ in self.stocks]
        self.data_fetcher = StockDataFetcher(codes)
        self.data_fetcher.data_updated.connect(self.on_data_updated)
        self.data_fetcher.start()
    
    def manual_refresh(self):
        """手动刷新"""
        if self.data_fetcher:
            self.data_fetcher.stop()
            # 不在这里wait，让它自然结束
            self.start_data_fetcher()
    
    def _fetch_minute_data_async(self, code, name, mini_chart=None):
        """后台异步获取分时数据，避免阻塞UI线程"""
        if code in self._minute_data_fetching:
            return  # 已经在获取中
        self._minute_data_fetching.add(code)

        def _do_fetch():
            try:
                data = self._generate_simulated_minute_data(code, name)
                if data:
                    self._minute_data_cache[code] = data
                    # 回到主线程更新UI（添加try/except保护，避免访问已销毁widget）
                    if mini_chart:
                        prices = [d['price'] for d in data]
                        prev_close = data[0].get('prev_close', prices[0] if prices else 0)
                        def _update_chart(mc=mini_chart, p=prices, pc=prev_close):
                            try:
                                mc.set_data(p, pc)
                            except Exception:
                                pass  # widget可能已被销毁，忽略
                        QTimer.singleShot(0, _update_chart)
            except Exception as e:
                print(f"异步获取{code}分时数据失败: {e}")
            finally:
                self._minute_data_fetching.discard(code)

        threading.Thread(target=_do_fetch, daemon=True).start()

    def _fetch_minute_data_batch(self, fetch_list):
        """串行批量获取分时数据，避免并发HTTP请求导致崩溃

        Args:
            fetch_list: [(code, name, mini_chart), ...]
        """
        # 过滤掉正在获取的股票
        to_fetch = [(c, n, mc) for c, n, mc in fetch_list if c not in self._minute_data_fetching]
        if not to_fetch:
            return
        for c, _, _ in to_fetch:
            self._minute_data_fetching.add(c)

        def _do_batch():
            for code, name, mini_chart in to_fetch:
                try:
                    data = self._generate_simulated_minute_data(code, name)
                    if data:
                        self._minute_data_cache[code] = data
                        if mini_chart:
                            prices = [d['price'] for d in data]
                            prev_close = data[0].get('prev_close', prices[0] if prices else 0)
                            def _update_chart(mc=mini_chart, p=prices, pc=prev_close):
                                try:
                                    mc.set_data(p, pc)
                                except Exception:
                                    pass  # widget可能已被销毁，忽略
                            QTimer.singleShot(0, _update_chart)
                except Exception as e:
                    print(f"批量获取{code}分时数据失败: {e}")
                finally:
                    self._minute_data_fetching.discard(code)

        threading.Thread(target=_do_batch, daemon=True).start()

    def _refresh_all_minute_charts(self):
        """定时刷新所有分时图（每1分钟），让盘中分时图随时间逐步更新"""
        # 非交易时间不刷新
        now = datetime.now()
        # 周末不刷新
        if now.weekday() >= 5:
            return
        hour, minute = now.hour, now.minute
        # 9:30-11:30, 13:00-15:00 之间才刷新
        is_morning = (hour == 9 and minute >= 30) or (hour == 10) or (hour == 11 and minute <= 30)
        is_afternoon = (hour == 13) or (hour == 14) or (hour == 15 and minute == 0)
        if not (is_morning or is_afternoon):
            return

        # 重新获取所有股票的分时数据
        for code, name in self.stocks:
            # 跳过正在获取的
            if code in self._minute_data_fetching:
                continue
            self._minute_data_fetching.add(code)
            mini_chart = self.excel_table._mini_charts.get(code) if hasattr(self.excel_table, '_mini_charts') else None

            def _do_fetch(c=code, n=name, mc=mini_chart):
                try:
                    data = self._generate_simulated_minute_data(c, n)
                    if data:
                        self._minute_data_cache[c] = data
                        # 更新迷你分时图
                        if mc:
                            try:
                                prices = [d['price'] for d in data]
                                prev_close = data[0].get('prev_close', prices[0] if prices else 0)
                                QTimer.singleShot(0, lambda: mc.set_data(prices, prev_close))
                            except Exception:
                                pass
                        # 更新全屏分时图（如果当前显示的就是这只股票）
                        if self.current_chart_stock == c:
                            real_data = self.stock_data.get(c, None)
                            QTimer.singleShot(0, lambda: self.chart_widget.set_minute_data(data, real_data))
                except Exception as e:
                    print(f"定时刷新{c}分时数据失败: {e}")
                finally:
                    self._minute_data_fetching.discard(c)

            threading.Thread(target=_do_fetch, daemon=True).start()

    def on_stock_double_clicked(self, code, name):
        """处理双击股票事件 - 切换到分时图标签页"""
        # 记录当前在走势图中显示的股票
        self.current_chart_stock = code

        # 切换到走势分析标签页
        self.tab_widget.setCurrentIndex(1)

        # 更新图表标题和股票代码
        self.chart_widget.stock_code = code
        self.chart_widget.stock_name = name

        # 检查是否需要刷新分时数据缓存（新的一天）
        today = datetime.now().strftime('%Y%m%d')
        if self._minute_data_cache_date != today:
            self._minute_data_cache = {}
            self._minute_data_cache_date = today

        # 优先使用缓存的分时数据，避免阻塞UI
        minute_data = self._minute_data_cache.get(code)
        if minute_data is None:
            # 缓存中没有，后台获取并更新图表
            def _do_fetch():
                data = self._generate_simulated_minute_data(code, name)
                if data:
                    self._minute_data_cache[code] = data
                    real_data = self.stock_data.get(code, None)
                    QTimer.singleShot(0, lambda: self.chart_widget.set_minute_data(data, real_data))
            threading.Thread(target=_do_fetch, daemon=True).start()
            return
        # 传入真实行情数据用于信息栏显示
        real_data = self.stock_data.get(code, None)
        self.chart_widget.set_minute_data(minute_data, real_data)

    def _fetch_real_minute_data(self, code, name):
        """从腾讯API获取真实分时数据

        接口: https://ifzq.gtimg.cn/appstock/app/minute/query
        数据格式: "HHMM price volume amount"
        返回: list of dicts [{'time': '09:30', 'price': xxx, 'volume': xxx, 'prev_close': xxx}, ...]
        """
        # 构造腾讯API的股票代码前缀
        if code.startswith('6') or code.startswith('5'):
            prefix = 'sh'
        else:
            prefix = 'sz'

        url = 'https://ifzq.gtimg.cn/appstock/app/minute/query'
        params = {'code': f'{prefix}{code}'}

        try:
            response = requests.get(url, params=params, timeout=10)
            # 响应是JSONP格式: "min_data=" 开头
            text = response.text
            if text.startswith('min_data='):
                text = text[9:]
            data = json.loads(text)

            stock_key = f'{prefix}{code}'
            stock_data = data.get('data', {}).get(stock_key, {})
            minute_lines = stock_data.get('data', {}).get('data', [])
            qt_fields = stock_data.get('qt', {}).get(stock_key, [])

            if not minute_lines:
                return None

            # 从qt字段获取昨收价
            prev_close = 0
            if len(qt_fields) > 4:
                prev_close = float(qt_fields[4])  # 昨收价

            # 解析分时数据（API返回的是累计成交量，需要转换为每分钟成交量）
            result = []
            prev_cum_vol = 0
            for line in minute_lines:
                parts = line.split()
                if len(parts) >= 3:
                    time_str = parts[0]  # "0930"
                    price = float(parts[1])
                    cum_vol = int(parts[2])  # 累计成交量
                    # 转换为每分钟成交量（同花顺风格）
                    per_min_vol = cum_vol - prev_cum_vol
                    if per_min_vol < 0:
                        per_min_vol = 0  # 防止异常数据
                    prev_cum_vol = cum_vol
                    # 格式化时间: "0930" -> "09:30"
                    formatted_time = f"{time_str[:2]}:{time_str[2:]}"
                    result.append({
                        'time': formatted_time,
                        'price': price,
                        'volume': per_min_vol,
                        'prev_close': prev_close,
                    })

            return result if result else None

        except Exception as e:
            print(f"获取{code}分时数据失败: {e}")
            return None

    def _generate_simulated_minute_data(self, code, name):
        """获取分时数据：优先使用真实数据，失败时回退到模拟数据"""
        # 优先获取真实数据
        real_data = self._fetch_real_minute_data(code, name)
        if real_data and len(real_data) >= 10:
            return real_data

        # 回退到模拟数据
        import random
        # 基础参数 - 从真实行情获取
        current_price = 10.0
        prev_close = 10.0
        real_high = 0
        real_low = 0
        if code in self.stock_data:
            current_price = float(self.stock_data[code].get('current_price', current_price))
            prev_close = float(self.stock_data[code].get('prev_close', current_price))
            real_high = float(self.stock_data[code].get('high', 0))
            real_low = float(self.stock_data[code].get('low', 0))

        # 时间序列：9:30-11:30 (121点) + 13:00-15:00 (120点) = 241点
        morning = []
        hour, minute = 9, 30
        for _ in range(121):
            morning.append(f"{hour:02d}:{minute:02d}")
            minute += 1
            if minute >= 60:
                hour += 1
                minute = 0

        afternoon = []
        hour, minute = 13, 0
        for _ in range(120):
            afternoon.append(f"{hour:02d}:{minute:02d}")
            minute += 1
            if minute >= 60:
                hour += 1
                minute = 0

        all_times = morning + afternoon
        n_points = len(all_times)  # 241

        # 使用股票代码+当前日期作为种子，保证每天数据一致，但每天会更新
        today_seed = hash(code + datetime.now().strftime('%Y%m%d')) % 100000
        random.seed(today_seed)

        # 目标总变化量
        target_delta = current_price - prev_close

        # 价格范围：以昨收价为中心，对称扩展
        # 取真实最高/最低价相对昨收的较大偏离作为对称范围
        if real_high > 0 and real_low > 0:
            up_range = abs(real_high - prev_close)
            down_range = abs(prev_close - real_low)
            display_range = max(up_range, down_range) * 1.1  # 留10%余量
        else:
            # 没有真实数据时，使用昨收价的±3%
            display_range = abs(prev_close) * 0.03

        max_price = prev_close + display_range
        min_price = prev_close - display_range

        # 生成布朗运动噪声
        volatility = display_range * 0.15  # 波动幅度为显示范围的15%
        noise = []
        current_noise = 0.0
        for i in range(n_points):
            current_noise += random.gauss(0, volatility * 0.3)
            current_noise *= 0.98  # 噪声回归，避免漂移过远
            noise.append(current_noise)

        # 构造价格 = 趋势线 + 噪声
        # 趋势线：从 prev_close 到 current_price 的平滑过渡
        prices = []
        for i in range(n_points):
            t = i / (n_points - 1)  # 0 到 1

            # 使用平滑的S形曲线过渡
            # 前50%时间完成约60%的趋势变化，后50%完成剩余40%
            if t < 0.5:
                trend_t = (t * 2) ** 1.2 * 0.6
            else:
                trend_t = 0.6 + ((t - 0.5) * 2) ** 0.9 * 0.4

            trend_price = prev_close + target_delta * trend_t

            # 叠加噪声
            p = trend_price + noise[i]

            # 限制在显示范围内
            p = max(min_price, min(max_price, p))
            prices.append(round(p, 3))

        # 强制第一个点=昨收价，最后一个点=当前价
        if prices:
            prices[0] = round(prev_close, 3)
            prices[-1] = round(current_price, 3)

        # 生成成交量（开盘和尾盘较大，中间较小）
        volumes = []
        for i in range(n_points):
            t_ratio = i / n_points
            if t_ratio < 0.15 or t_ratio > 0.85:
                time_factor = 1.5 + random.random() * 0.5
            elif t_ratio < 0.3 or t_ratio > 0.7:
                time_factor = 1.0 + random.random() * 0.3
            else:
                time_factor = 0.5 + random.random() * 0.3

            base_vol = 5000 * (prev_close / 10.0)
            v = int(base_vol * time_factor * (0.5 + random.random()))
            volumes.append(v)

        # 组装数据
        data = []
        for i, t in enumerate(all_times):
            data.append({
                'time': t,
                'price': prices[i],
                'volume': volumes[i],
                'amount': prices[i] * volumes[i],
                'prev_close': prev_close,
            })
        return data

    def fetch_and_draw_minute_chart(self, code):
        """获取并绘制分时图数据"""
        # 这里应该调用API获取分时数据
        # 由于新浪财经分时数据需要特殊处理，这里先使用模拟数据
        pass
    
    def is_trading_day(self, date=None):
        """判断是否为交易日"""
        if date is None:
            date = datetime.now()
        
        # 简单判断：周一到周五为交易日（不考虑节假日）
        return date.weekday() < 5
    
    def get_last_trading_day(self):
        """获取上一个交易日"""
        date = datetime.now()
        
        # 向前查找最近的交易日
        for i in range(1, 8):
            check_date = date - timedelta(days=i)
            if self.is_trading_day(check_date):
                return check_date
        
        return date - timedelta(days=1)  # 默认返回昨天
    
    def show_trade_dialog(self, code=None, name=None):
        """显示买卖对话框 - 支持买入/卖出切换，可指定默认股票"""
        from PyQt5.QtWidgets import QButtonGroup
        from PyQt5.QtGui import QIntValidator, QDoubleValidator

        dialog = QDialog(self)
        dialog.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint)
        dialog.setFixedSize(400, 380)

        main_layout = QVBoxLayout(dialog)
        main_layout.setSpacing(12)

        # ===== 标题 =====
        title_label = QLabel("股票交易")
        title_label.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
        title_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_label)

        # ===== 买卖切换按钮 =====
        mode_layout = QHBoxLayout()
        mode_layout.addStretch()
        self.trade_mode_group = QButtonGroup(dialog)
        buy_btn = QPushButton("买入")
        buy_btn.setCheckable(True)
        buy_btn.setFixedSize(80, 32)
        buy_btn.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        sell_btn = QPushButton("卖出")
        sell_btn.setCheckable(True)
        sell_btn.setFixedSize(80, 32)
        sell_btn.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        self.trade_mode_group.addButton(buy_btn, 0)
        self.trade_mode_group.addButton(sell_btn, 1)
        mode_layout.addWidget(buy_btn)
        mode_layout.addWidget(sell_btn)
        mode_layout.addStretch()
        main_layout.addLayout(mode_layout)

        # ===== 分割线 =====
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("border: 1px solid #ddd;")
        main_layout.addWidget(line)

        # ===== 股票代码 =====
        code_layout = QHBoxLayout()
        code_label = QLabel("代码:")
        code_label.setFixedWidth(60)
        code_input = QLineEdit()
        code_input.setPlaceholderText("输入6位代码")
        if code:
            code_input.setText(code)
            code_input.setReadOnly(True)
        code_layout.addWidget(code_label)
        code_layout.addWidget(code_input)
        main_layout.addLayout(code_layout)

        # ===== 名称（自动填充）=====
        name_layout = QHBoxLayout()
        name_label = QLabel("名称:")
        name_label.setFixedWidth(60)
        name_input = QLineEdit()
        name_input.setReadOnly(True)
        if name:
            name_input.setText(name)
        name_layout.addWidget(name_label)
        name_layout.addWidget(name_input)
        main_layout.addLayout(name_layout)

        # ===== 持仓信息标签（卖出模式时显示）=====
        self.position_info_label = QLabel()
        self.position_info_label.setStyleSheet("color: #333; font-size: 13px; font-weight: bold; padding: 6px; background: #f0f0f0; border-radius: 4px;")
        self.position_info_label.setVisible(False)
        main_layout.addWidget(self.position_info_label)

        # ===== 数量输入 =====
        quantity_layout = QHBoxLayout()
        quantity_label = QLabel("数量:")
        quantity_label.setFixedWidth(60)
        quantity_input = QLineEdit()
        quantity_input.setPlaceholderText("100的整数倍")
        quantity_input.setValidator(QIntValidator(1, 10000000))
        quantity_layout.addWidget(quantity_label)
        quantity_layout.addWidget(quantity_input)
        main_layout.addLayout(quantity_layout)

        # ===== 价格输入 =====
        price_layout = QHBoxLayout()
        price_label = QLabel("价格:")
        price_label.setFixedWidth(60)
        price_input = QLineEdit()
        price_input.setPlaceholderText("输入价格")
        price_input.setValidator(QDoubleValidator(0, 99999.999, 3))
        price_layout.addWidget(price_label)
        price_layout.addWidget(price_input)
        main_layout.addLayout(price_layout)

        # ===== 费用显示 =====
        self.fee_label = QLabel()
        self.fee_label.setStyleSheet("color: #555; font-size: 12px; font-weight: bold; padding: 4px;")
        main_layout.addWidget(self.fee_label)

        # ===== 按钮 =====
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        ok_btn = QPushButton("确认")
        ok_btn.setFixedSize(80, 32)
        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedSize(80, 32)
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        main_layout.addLayout(btn_layout)

        # ===== 内部状态 =====
        self._trade_dialog = dialog
        self._trade_code_input = code_input
        self._trade_name_input = name_input
        self._trade_quantity_input = quantity_input
        self._trade_price_input = price_input
        self._trade_fee_label = self.fee_label
        self._trade_mode = 0  # 0=买入, 1=卖出

        # ===== 自动获取名称 =====
        self.fetch_status_label = QLabel("")
        self.fetch_status_label.setStyleSheet("color: #888; font-size: 10px;")
        main_layout.addWidget(self.fetch_status_label)

        def update_trade_mode(mode):
            """切换买卖模式"""
            self._trade_mode = mode
            if mode == 0:
                dialog.setWindowTitle("买入股票")
                buy_btn.setStyleSheet("background: #d32f2f; color: white; border: none; border-radius: 4px;")
                sell_btn.setStyleSheet("background: transparent; color: #666; border: 1px solid #ccc; border-radius: 4px;")
                # 买入模式
                code_input.setReadOnly(False if not code else True)
                self.position_info_label.setVisible(False)
                quantity_input.setPlaceholderText("请输入买入数量")
                quantity_input.setValidator(QIntValidator(1, 10000000))
                # 如果有持仓，合并买入
                if code and code in self.positions:
                    pos = self.positions[code]
                    qty = pos.get('quantity', 0)
                    if qty > 0:
                        quantity_input.setPlaceholderText(f"合并买入，当前持仓{qty}股")
            else:
                dialog.setWindowTitle("卖出股票")
                sell_btn.setStyleSheet("background: #388e3c; color: white; border: none; border-radius: 4px;")
                buy_btn.setStyleSheet("background: transparent; color: #666; border: 1px solid #ccc; border-radius: 4px;")
                # 卖出模式
                if code and code in self.positions:
                    pos = self.positions[code]
                    qty = pos.get('quantity', 0)
                    cost = pos.get('cost_price', 0)
                    if qty > 0:
                        self.position_info_label.setText(f"持仓: {qty}股  |  成本价: {cost:.3f}  |  最高可卖: {qty}股")
                        self.position_info_label.setVisible(True)
                        quantity_input.setPlaceholderText(f"最多可卖 {qty} 股")
                        quantity_input.setValidator(QIntValidator(1, qty))
                        quantity_input.setText(str(qty))
                    else:
                        self.position_info_label.setText("无持仓，无法卖出")
                        self.position_info_label.setVisible(True)
                        quantity_input.clear()
                else:
                    self.position_info_label.setText("未找到持仓数据")
                    self.position_info_label.setVisible(True)
                    quantity_input.clear()
            update_fee()

        def update_fee():
            """实时计算费用"""
            try:
                qty = int(quantity_input.text().strip() or '0')
                price = float(price_input.text().strip() or '0')
                if qty <= 0 or price <= 0:
                    self._trade_fee_label.setText("")
                    return
                amount = qty * price
                if self._trade_mode == 0:
                    fees = self.calculate_buy_fees(qty, price)
                    total = fees['total_fees']
                    self._trade_fee_label.setText(
                        f"买入金额: {amount:,.2f}  |  费用: {total:,.2f}  |  实际成本: {amount+total:,.2f}"
                    )
                else:
                    fees = self.calculate_sell_fees(qty, price)
                    total = fees['total_fees']
                    self._trade_fee_label.setText(
                        f"卖出金额: {amount:,.2f}  |  费用: {total:,.2f}  |  实际收益: {amount-total:,.2f}"
                    )
            except:
                self._trade_fee_label.setText("")

        def on_name_lookup():
            """查询股票名称"""
            c = code_input.text().strip()
            if not c or len(c) != 6:
                name_input.clear()
                self.fetch_status_label.setText("")
                return
            self.fetch_status_label.setText("正在查询...")
            try:
                stock_name = StockInfoFetcher.get_stock_name(c)
                if stock_name:
                    name_input.setText(stock_name)
                    self.fetch_status_label.setText(f"✓ {stock_name}")
                    self.fetch_status_label.setStyleSheet("color: #4caf50; font-size: 10px;")
                else:
                    self.fetch_status_label.setText("✗ 未找到")
                    self.fetch_status_label.setStyleSheet("color: #f44336; font-size: 10px;")
            except:
                self.fetch_status_label.setText("查询失败")
                self.fetch_status_label.setStyleSheet("color: #f44336; font-size: 10px;")

        def on_code_changed():
            """代码变化时自动查询名称"""
            if not code_input.isReadOnly():
                name_input.clear()
                self.fetch_status_label.setText("")
                update_trade_mode(self._trade_mode)

        # 连接信号
        buy_btn.clicked.connect(lambda: update_trade_mode(0))
        sell_btn.clicked.connect(lambda: update_trade_mode(1))
        code_input.textChanged.connect(on_code_changed)
        code_input.editingFinished.connect(on_name_lookup)
        quantity_input.textChanged.connect(update_fee)
        price_input.textChanged.connect(update_fee)

        def on_accept():
            """确认交易"""
            c = code_input.text().strip()
            n = name_input.text().strip()
            q_str = quantity_input.text().strip()
            p_str = price_input.text().strip()
            if self._trade_mode == 0:
                self.buy_stock(c, n, q_str, p_str, dialog)
            else:
                self.sell_stock(c, n, q_str, p_str, dialog)

        ok_btn.clicked.connect(on_accept)
        cancel_btn.clicked.connect(dialog.reject)

        # 初始化：默认买入模式，如果有持仓数据则默认卖出
        if code and code in self.positions:
            pos = self.positions.get(code, {})
            if pos.get('quantity', 0) > 0:
                sell_btn.setChecked(True)
                update_trade_mode(1)
            else:
                buy_btn.setChecked(True)
                update_trade_mode(0)
        else:
            buy_btn.setChecked(True)
            update_trade_mode(0)

        # 自动查询名称（如果只给了代码）
        if code and not name:
            on_name_lookup()

        # 自动填充实时价格
        if code and code in self.stock_data:
            rt = self.stock_data[code].get('current_price', 0)
            if rt > 0:
                price_input.setText(f"{rt:.3f}")

        dialog.exec_()

    def show_buy_dialog(self):
        """显示买入股票对话框"""
        from PyQt5.QtGui import QIntValidator, QDoubleValidator
        
        dialog = QDialog(self)
        dialog.setWindowTitle("买入股票")
        dialog.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint)
        dialog.setFixedSize(350, 280)
        
        layout = QVBoxLayout(dialog)
        layout.setSpacing(10)
        
        # 股票代码输入
        code_layout = QHBoxLayout()
        code_label = QLabel("代码:")
        code_label.setFixedWidth(60)
        code_input = QLineEdit()
        code_input.setPlaceholderText("例如: 600519")
        code_layout.addWidget(code_label)
        code_layout.addWidget(code_input)
        layout.addLayout(code_layout)
        
        # 名称输入（自动填充）
        name_layout = QHBoxLayout()
        name_label = QLabel("名称:")
        name_label.setFixedWidth(60)
        name_input = QLineEdit()
        name_input.setPlaceholderText("输入代码后自动获取")
        name_input.setReadOnly(True)
        name_layout.addWidget(name_label)
        name_layout.addWidget(name_input)
        layout.addLayout(name_layout)
        
        # 买入数量输入
        quantity_layout = QHBoxLayout()
        quantity_label = QLabel("数量:")
        quantity_label.setFixedWidth(60)
        quantity_input = QLineEdit()
        quantity_input.setPlaceholderText("请输入买入数量")
        quantity_input.setValidator(QIntValidator(1, 10000000))
        quantity_layout.addWidget(quantity_label)
        quantity_layout.addWidget(quantity_input)
        layout.addLayout(quantity_layout)
        
        # 买入价格输入
        price_layout = QHBoxLayout()
        price_label = QLabel("价格:")
        price_label.setFixedWidth(60)
        price_input = QLineEdit()
        price_input.setPlaceholderText("请输入买入价格")
        price_input.setValidator(QDoubleValidator(0, 99999.999, 3))
        price_layout.addWidget(price_label)
        price_layout.addWidget(price_input)
        layout.addLayout(price_layout)
        
        # 状态提示
        self.fetch_status_label = QLabel("")
        self.fetch_status_label.setStyleSheet("color: #666; font-size: 10px;")
        layout.addWidget(self.fetch_status_label)
        
        layout.addStretch()
        
        # 按钮
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(lambda: self.buy_stock(code_input.text(), name_input.text(), quantity_input.text(), price_input.text(), dialog))
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        # 当代码输入改变时，自动获取名称
        code_input.textChanged.connect(lambda: self.auto_fetch_name(code_input.text(), name_input))
        
        dialog.exec_()
    
    def auto_fetch_name(self, code, name_input):
        """自动获取股票名称（后台异步，不阻塞UI）"""
        code = code.strip()
        if not code:
            name_input.clear()
            self.fetch_status_label.setText("")
            return

        # 港股代码格式：HK + 5位数字，如 HK2513
        if code.upper().startswith('HK'):
            if len(code) < 7:  # HK + 至少1位
                self.fetch_status_label.setText("")
                return
            query_code = code
        else:
            # A股代码6位
            if len(code) != 6:
                return
            query_code = code

        # 显示获取中状态
        self.fetch_status_label.setText("正在获取...")

        # 后台异步获取，避免阻塞UI
        def _do_fetch():
            try:
                name = StockInfoFetcher.get_stock_name(query_code)
                # 回到主线程更新UI
                def _update_ui():
                    if name:
                        name_input.setText(name)
                        self.fetch_status_label.setText(f"✓ {name}")
                        self.fetch_status_label.setStyleSheet("color: #4caf50; font-size: 10px;")
                    else:
                        self.fetch_status_label.setText("✗ 未找到")
                        self.fetch_status_label.setStyleSheet("color: #f44336; font-size: 10px;")
                        name_input.setReadOnly(False)
                        name_input.setFocus()
                QTimer.singleShot(0, _update_ui)
            except Exception:
                QTimer.singleShot(0, lambda: self.fetch_status_label.setText("✗ 错误"))

        threading.Thread(target=_do_fetch, daemon=True).start()

    def calculate_buy_fees(self, quantity, price):
        """计算买入费用"""
        amount = quantity * price
        
        # 券商佣金：万1.2，最低5元兜底
        commission = amount * 0.00012
        commission = max(commission, 5.0)
        
        total_fees = commission
        return {
            'commission': commission,
            'total_fees': total_fees
        }
    
    def calculate_sell_fees(self, quantity, price):
        """计算卖出费用"""
        amount = quantity * price
        
        # 印花税：万5（仅卖出）
        stamp_tax = amount * 0.0005
        
        # 券商佣金：万1.2，最低5元兜底
        commission = amount * 0.00012
        commission = max(commission, 5.0)
        
        total_fees = stamp_tax + commission
        return {
            'stamp_tax': stamp_tax,
            'commission': commission,
            'total_fees': total_fees
        }
    
    def buy_stock(self, code, name, quantity_str, price_str, dialog):
        """买入股票"""
        from PyQt5.QtWidgets import QMessageBox
        
        code = code.strip()
        if not code:
            QMessageBox.warning(self, "提示", "请输入股票代码")
            return
        
        if not name:
            name = code
        
        try:
            quantity = int(quantity_str.strip())
            price = float(price_str.strip())
        except ValueError:
            QMessageBox.warning(self, "提示", "请输入有效的数量和价格")
            return
        
        if quantity <= 0:
            QMessageBox.warning(self, "提示", "买入数量必须大于0")
            return
        
        if price <= 0:
            QMessageBox.warning(self, "提示", "买入价格必须大于0")
            return
        
        # 计算买入费用
        buy_fees = self.calculate_buy_fees(quantity, price)
        
        # 实际成本 = 买入金额 + 买入费用
        actual_cost = (quantity * price) + buy_fees['total_fees']
        actual_price_per_share = actual_cost / quantity
        
        # 检查是否已存在
        exists = any(c == code for c, _ in self.stocks)
        
        if exists:
            # 已存在，追加买入，加权平均计算成本
            old_quantity = self.positions[code].get('quantity', 0)
            old_cost = self.positions[code].get('cost_price', 0)
            
            total_cost = (old_quantity * old_cost) + actual_cost
            total_quantity = old_quantity + quantity
            new_cost = total_cost / total_quantity
            
            self.positions[code]['quantity'] = total_quantity
            self.positions[code]['cost_price'] = new_cost
            # 追加买入不改变 is_today_added，保留原值
        else:
            # 不存在，新建记录
            self.stocks.append((code, name))
            self.positions[code] = {
                'name': name,
                'quantity': quantity,
                'cost_price': actual_price_per_share,
                'market_value': 0.0,
                'total_profit': 0.0,
                'total_profit_percent': 0.0,
                'today_profit': 0.0,
                'today_profit_percent': 0.0,
                'current_price': 0.0,
                'change_percent': 0.0,
                'is_today_added': True
            }
        
        # 获取实时价格并计算盈亏
        if code in self.stock_data:
            realtime_price = self.stock_data[code].get('current_price', 0)
            if realtime_price > 0:
                current_qty = self.positions[code]['quantity']
                current_cost = self.positions[code]['cost_price']
                self.positions[code]['current_price'] = realtime_price
                self.positions[code]['market_value'] = current_qty * realtime_price
                self.positions[code]['total_profit'] = (realtime_price - current_cost) * current_qty
                self.positions[code]['total_profit_percent'] = ((realtime_price - current_cost) / current_cost) * 100
                
                is_today_added = self.positions[code].get('is_today_added', False)
                if is_today_added:
                    self.positions[code]['today_profit'] = self.positions[code]['total_profit']
                    self.positions[code]['today_profit_percent'] = self.positions[code]['total_profit_percent']
                else:
                    change = self.stock_data[code].get('change', 0)
                    self.positions[code]['today_profit'] = change * current_qty
                    self.positions[code]['today_profit_percent'] = self.stock_data[code].get('change_percent', 0)
        
        self.save_stocks()
        self.save_positions()
        self.refresh_table()
        self.update_stock_count()
        
        # 异步重启数据获取器（不阻塞UI）
        if self.data_fetcher:
            self.data_fetcher.stop()
            self.start_data_fetcher()
        
        dialog.accept()
    
    def show_sell_dialog(self):
        """显示卖出股票对话框"""
        from PyQt5.QtWidgets import QMessageBox, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QDialogButtonBox
        from PyQt5.QtGui import QIntValidator, QDoubleValidator
        
        # 获取选中的股票
        selected_rows = self.excel_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "提示", "请先选中要卖出的股票")
            return
        
        # 获取第一个选中的股票代码
        row = selected_rows[0].row()
        item = self.excel_table.item(row, 0)
        if not item:
            QMessageBox.warning(self, "提示", "无法获取选中股票的信息")
            return
        
        text = item.text()
        parts = text.split('\n')
        if len(parts) < 2:
            QMessageBox.warning(self, "提示", "无法获取选中股票的代码")
            return
        
        code = parts[1]
        name = parts[0]
        
        # 获取持仓信息
        position = self.positions.get(code, {})
        quantity = position.get('quantity', 0)
        cost_price = position.get('cost_price', 0)
        
        if quantity <= 0:
            QMessageBox.warning(self, "提示", f"{name}({code}) 没有持仓")
            return
        
        dialog = QDialog(self)
        dialog.setWindowTitle("卖出股票")
        dialog.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint)
        dialog.setFixedSize(350, 250)
        
        layout = QVBoxLayout(dialog)
        layout.setSpacing(10)
        
        # 股票信息
        title_label = QLabel(f"{name} ({code})")
        title_label.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        layout.addWidget(title_label)
        
        # 持仓信息
        info_layout = QHBoxLayout()
        info_label = QLabel(f"持仓数量: {quantity} | 成本价: {cost_price:.3f}")
        info_label.setStyleSheet("color: #333; font-size: 13px; font-weight: bold;")
        info_layout.addWidget(info_label)
        info_layout.addStretch()
        layout.addLayout(info_layout)
        
        # 卖出数量输入
        quantity_layout = QHBoxLayout()
        quantity_label = QLabel("数量:")
        quantity_label.setFixedWidth(60)
        quantity_input = QLineEdit()
        quantity_input.setPlaceholderText(f"最多可卖 {quantity}")
        quantity_input.setValidator(QIntValidator(1, quantity))
        quantity_input.setText(str(quantity))
        quantity_layout.addWidget(quantity_label)
        quantity_layout.addWidget(quantity_input)
        layout.addLayout(quantity_layout)
        
        # 卖出价格输入
        price_layout = QHBoxLayout()
        price_label = QLabel("价格:")
        price_label.setFixedWidth(60)
        price_input = QLineEdit()
        price_input.setPlaceholderText("请输入卖出价格")
        price_input.setValidator(QDoubleValidator(0, 99999.999, 3))
        
        # 尝试获取当前实时价格作为默认值
        if code in self.stock_data:
            realtime_price = self.stock_data[code].get('current_price', 0)
            if realtime_price > 0:
                price_input.setText(f"{realtime_price:.3f}")
        
        price_layout.addWidget(price_label)
        price_layout.addWidget(price_input)
        layout.addLayout(price_layout)
        
        layout.addStretch()
        
        # 按钮
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(lambda: self.sell_stock(code, name, quantity_input.text(), price_input.text(), dialog))
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        dialog.exec_()
    
    def sell_stock(self, code, name, quantity_str, price_str, dialog):
        """卖出股票"""
        from PyQt5.QtWidgets import QMessageBox
        
        try:
            quantity = int(quantity_str.strip())
            price = float(price_str.strip())
        except ValueError:
            QMessageBox.warning(self, "提示", "请输入有效的数量和价格")
            return
        
        if quantity <= 0:
            QMessageBox.warning(self, "提示", "卖出数量必须大于0")
            return
        
        if price <= 0:
            QMessageBox.warning(self, "提示", "卖出价格必须大于0")
            return
        
        # 获取持仓信息
        position = self.positions.get(code, {})
        current_quantity = position.get('quantity', 0)
        cost_price = position.get('cost_price', 0)
        
        if quantity > current_quantity:
            QMessageBox.warning(self, "提示", f"卖出数量不能超过持仓数量 {current_quantity}")
            return
        
        # 获取昨收价（用于计算当日盈亏）
        prev_close = position.get('prev_close', 0)
        if prev_close <= 0 and code in self.stock_data:
            data = self.stock_data[code]
            current_price = data.get('current_price', 0)
            change = data.get('change', 0)
            if current_price > 0 and change != 0:
                prev_close = current_price - change
        
        # 计算卖出费用
        sell_fees = self.calculate_sell_fees(quantity, price)
        
        # 计算实际收益（总盈亏）
        sell_amount = quantity * price
        cost_amount = quantity * cost_price
        actual_profit = sell_amount - cost_amount - sell_fees['total_fees']
        
        # 计算当日盈亏（基于昨收价）
        if prev_close > 0:
            today_profit = (price - prev_close) * quantity - sell_fees['total_fees']
            today_pct = ((price - prev_close) / prev_close) * 100
        else:
            today_profit = actual_profit
            today_pct = ((price - cost_price) / cost_price) * 100
        
        # 更新持仓
        if quantity == current_quantity:
            # 全部卖出，设置is_sold标记（第二日程序启动时自动清除）
            # 保留盈亏数据，用于当日统计
            self.positions[code]['quantity'] = 0
            self.positions[code]['market_value'] = 0.0
            self.positions[code]['is_sold'] = True
            # 更新总盈亏（实际收益）和当日盈亏（基于昨收价）
            self.positions[code]['total_profit'] = actual_profit
            self.positions[code]['total_profit_percent'] = ((price - cost_price) / cost_price) * 100
            self.positions[code]['today_profit'] = today_profit
            self.positions[code]['today_profit_percent'] = today_pct
            
            message = f"已全部卖出 {name}({code})\n"
            message += f"卖出金额: {sell_amount:,.2f}\n"
            message += f"成本金额: {cost_amount:,.2f}\n"
            message += f"费用: {sell_fees['total_fees']:,.2f}\n"
            message += f"  - 印花税: {sell_fees['stamp_tax']:,.2f}\n"
            message += f"  - 佣金: {sell_fees['commission']:,.2f}\n"
            message += f"实际收益: {actual_profit:+.2f}"
            QMessageBox.information(self, "成功", message)
        else:
            # 部分卖出，更新持仓数量
            self.positions[code]['quantity'] = current_quantity - quantity
            
            # 重新计算市值和盈亏
            current_price = self.positions[code].get('current_price', 0)
            if current_price > 0:
                new_qty = self.positions[code]['quantity']
                self.positions[code]['market_value'] = new_qty * current_price
                self.positions[code]['total_profit'] = (current_price - cost_price) * new_qty
                self.positions[code]['total_profit_percent'] = ((current_price - cost_price) / cost_price) * 100
            
            message = f"已卖出 {quantity} 股 {name}({code})\n"
            message += f"卖出金额: {sell_amount:,.2f}\n"
            message += f"成本金额: {cost_amount:,.2f}\n"
            message += f"费用: {sell_fees['total_fees']:,.2f}\n"
            message += f"  - 印花税: {sell_fees['stamp_tax']:,.2f}\n"
            message += f"  - 佣金: {sell_fees['commission']:,.2f}\n"
            message += f"实际收益: {actual_profit:+.2f}"
            QMessageBox.information(self, "成功", message)
        
        self.save_stocks()
        self.save_positions()
        self.refresh_table()
        self.update_stock_count()
        
        # 异步重启数据获取器（不阻塞UI）
        if self.data_fetcher:
            self.data_fetcher.stop()
            self.start_data_fetcher()
        
        dialog.accept()
    
    def _build_position_from_row(self, row, col_map):
        """从导入行中解析股票持仓数据并返回 (code, name, position_dict)"""
        code = str(row[col_map.get('证券代码', -1)]).strip() if col_map.get('证券代码', -1) >= 0 else ''
        name = str(row[col_map.get('证券名称', -1)]).strip() if col_map.get('证券名称', -1) >= 0 else ''
        quantity_str = str(row[col_map.get('股票余额', -1)]).strip() if col_map.get('股票余额', -1) >= 0 else '0'
        cost_str = str(row[col_map.get('参考成本', -1)]).strip() if col_map.get('参考成本', -1) >= 0 else '0'
        profit_pct_str = str(row[col_map.get('盈亏比例(%)', -1)]).strip() if col_map.get('盈亏比例(%)', -1) >= 0 else '0'
        total_profit_str = str(row[col_map.get('总盈亏', -1)]).strip() if col_map.get('总盈亏', -1) >= 0 else '0'
        today_profit_str = str(row[col_map.get('当日盈亏', -1)]).strip() if col_map.get('当日盈亏', -1) >= 0 else '0'
        today_profit_pct_str = str(row[col_map.get('当日盈亏比(%)', -1)]).strip() if col_map.get('当日盈亏比(%)', -1) >= 0 else '0'
        market_value_str = str(row[col_map.get('市值', -1)]).strip() if col_map.get('市值', -1) >= 0 else '0'
        current_price_str = str(row[col_map.get('市价', -1)]).strip() if col_map.get('市价', -1) >= 0 else '0'
        today_buy_str = str(row[col_map.get('当日买入', -1)]).strip() if col_map.get('当日买入', -1) >= 0 else '0'

        if not code or not name:
            return None, None, None

        try:
            quantity = int(float(quantity_str))
            cost_price = float(cost_str)
            profit_pct = float(profit_pct_str)
            total_profit = float(total_profit_str)
            today_profit = float(today_profit_str)
            today_profit_pct = float(today_profit_pct_str)
            market_value = float(market_value_str)
            current_price = float(current_price_str)
            today_buy = float(today_buy_str)
        except:
            quantity = 0; cost_price = 0; profit_pct = 0
            total_profit = 0; today_profit = 0; today_profit_pct = 0
            market_value = 0; current_price = 0; today_buy = 0

        pos = {
            'name': name, 'quantity': quantity, 'cost_price': cost_price,
            'market_value': market_value, 'total_profit': total_profit,
            'total_profit_percent': profit_pct, 'today_profit': today_profit,
            'today_profit_percent': today_profit_pct, 'current_price': current_price
        }

        # 关键：根据文件字段标记 is_sold 和 is_today_added
        if quantity == 0 and (total_profit != 0 or today_profit != 0):
            pos['is_sold'] = True  # 清仓股：数量为0但有盈亏数据
        if today_buy > 0:
            pos['is_today_added'] = True  # 当日有买入

        return code, name, pos

    def import_from_csv(self):
        """导入持仓数据 - 支持CSV/TSV/XLS格式"""
        from PyQt5.QtWidgets import QFileDialog

        # 打开文件选择对话框，支持多种格式
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择持仓文件",
            "",
            "持仓文件 (*.csv *.xls *.xlsx *.tsv);;CSV Files (*.csv);;Excel Files (*.xls *.xlsx);;TSV Files (*.tsv);;All Files (*)"
        )

        if not file_path:
            return

        self.last_import_file = file_path
        self.save_config()

        # 先询问用户如何处理重复股票（在读取文件之前）
        from PyQt5.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self,
            "重复股票处理方式",
            "发现重复股票时，选择处理方式：\n\n"
            "【是】覆盖 - 用文件中的数据更新现有股票\n"
            "【否】忽略 - 保留现有数据，跳过重复股票",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        overwrite_mode = (reply == QMessageBox.Yes)

        try:
            ext = file_path.lower().rsplit('.', 1)[-1]

            if ext == 'xls':
                # TSV 或旧版 Excel - 先尝试 xlrd，失败则当 TSV 处理
                rows = self._read_xls_or_tsv(file_path)
            elif ext == 'xlsx':
                rows = self._read_xlsx(file_path)
            else:
                # CSV/TSV
                rows = self._read_csv_or_tsv(file_path)

            if not rows:
                QMessageBox.warning(self, "提示", "文件中没有有效数据")
                return

            # 解析列名
            headers = rows[0]
            data_rows = rows[1:]

            # 建立列名到索引的映射
            col_map = {}
            for i, h in enumerate(headers):
                h_clean = str(h).strip()
                col_map[h_clean] = i

            new_stocks = []
            new_positions = {}
            skipped_count = 0
            updated_count = 0

            for row in data_rows:
                code, name, pos = self._build_position_from_row(row, col_map)
                if code is None:
                    continue

                # 检查是否已存在
                exists = False
                for existing_code, existing_name in self.stocks:
                    if existing_code == code or (existing_name == name and len(name) >= 2):
                        exists = True
                        break

                if exists:
                    if overwrite_mode:
                        self.positions[code] = pos
                        for i, (ec, en) in enumerate(self.stocks):
                            if ec == code:
                                self.stocks[i] = (code, name)
                                break
                        updated_count += 1
                    else:
                        skipped_count += 1
                    continue

                new_stocks.append((code, name))
                new_positions[code] = pos
            
            if not new_stocks and updated_count == 0:
                if skipped_count > 0:
                    QMessageBox.information(self, "提示", f"文件中的 {skipped_count} 只股票都已存在，无需重复导入。")
                else:
                    QMessageBox.warning(self, "提示", "文件中没有有效数据")
                return
            
            self.stocks.extend(new_stocks)
            self.positions.update(new_positions)
            self.save_stocks()
            self.save_positions()
            self.load_positions()
            
            self.refresh_table()
            self.update_stock_count()
            
            if self.data_fetcher:
                self.data_fetcher.stop()
                self.start_data_fetcher()
            
            message = f"导入完成！\n\n新增：{len(new_stocks)} 只\n"
            if updated_count > 0:
                message += f"更新：{updated_count} 只\n"
            if skipped_count > 0:
                message += f"跳过：{skipped_count} 只\n"
            message += "\n数据已保存，下次启动自动加载。"
            QMessageBox.information(self, "成功", message)
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"导入失败：{str(e)}")
    
    def _read_csv_or_tsv(self, file_path):
        """读取CSV或TSV文件，返回二维列表"""
        import csv
        # 自动检测编码
        encoding = 'utf-8-sig'
        try:
            with open(file_path, 'r', encoding='utf-8-sig') as f:
                f.read(1024)
        except UnicodeDecodeError:
            encoding = 'gbk'
        
        # 检测分隔符：先读前几行判断是 tab 还是 comma
        with open(file_path, 'r', encoding=encoding) as f:
            sample = f.read(2048)
        delimiter = '\t' if '\t' in sample else ','
        
        rows = []
        with open(file_path, 'r', encoding=encoding) as f:
            reader = csv.reader(f, delimiter=delimiter)
            for row in reader:
                rows.append(row)
        return rows
    
    def _read_xls_or_tsv(self, file_path):
        """读取XLS文件，如果失败则当作TSV处理"""
        try:
            import xlrd
            wb = xlrd.open_workbook(file_path)
            ws = wb.sheet_by_index(0)
            rows = []
            for r in range(ws.nrows):
                rows.append([ws.cell_value(r, c) for c in range(ws.ncols)])
            return rows
        except Exception:
            # 不是真正的xls，当TSV处理
            return self._read_csv_or_tsv(file_path)
    
    def _read_xlsx(self, file_path):
        """读取XLSX文件"""
        try:
            import openpyxl
            wb = openpyxl.load_workbook(file_path, read_only=True)
            ws = wb.active
            rows = []
            for row in ws.iter_rows(values_only=True):
                rows.append(list(row))
            wb.close()
            return rows
        except ImportError:
            raise Exception("读取xlsx需要安装openpyxl：pip install openpyxl")
    
    def sync_from_last_import(self):
        """从上次导入的文件同步数据（覆盖模式+新增）"""
        if not hasattr(self, 'last_import_file') or not self.last_import_file:
            QMessageBox.warning(self, "提示", "尚未导入过文件，请先使用「导入」功能。")
            return
        
        import os
        if not os.path.exists(self.last_import_file):
            QMessageBox.warning(self, "提示", f"上次导入的文件已不存在：\n{self.last_import_file}")
            return
        
        try:
            ext = self.last_import_file.lower().rsplit('.', 1)[-1]
            
            if ext == 'xls':
                rows = self._read_xls_or_tsv(self.last_import_file)
            elif ext == 'xlsx':
                rows = self._read_xlsx(self.last_import_file)
            else:
                rows = self._read_csv_or_tsv(self.last_import_file)
            
            if not rows:
                QMessageBox.warning(self, "提示", "文件中没有有效数据")
                return
            
            headers = rows[0]
            data_rows = rows[1:]
            
            col_map = {}
            for i, h in enumerate(headers):
                col_map[str(h).strip()] = i
            
            new_stocks = []
            new_positions = {}
            updated_count = 0
            
            for row in data_rows:
                code, name, pos_data = self._build_position_from_row(row, col_map)
                if code is None:
                    continue

                exists = False
                for existing_code, existing_name in self.stocks:
                    if existing_code == code or (existing_name == name and len(name) >= 2):
                        exists = True
                        break

                if exists:
                    self.positions[code] = pos_data
                    for i, (ec, en) in enumerate(self.stocks):
                        if ec == code:
                            self.stocks[i] = (code, name)
                            break
                    updated_count += 1
                else:
                    new_stocks.append((code, name))
                    new_positions[code] = pos_data
            
            if not new_stocks and updated_count == 0:
                QMessageBox.information(self, "提示", "文件中没有有效数据。")
                return
            
            self.stocks.extend(new_stocks)
            self.positions.update(new_positions)
            self.save_stocks()
            self.save_positions()
            self.load_positions()
            self.refresh_table()
            self.update_stock_count()
            
            if self.data_fetcher:
                self.data_fetcher.stop()
                self.start_data_fetcher()
            
            message = f"同步完成！\n\n新增：{len(new_stocks)} 只\n"
            if updated_count > 0:
                message += f"更新：{updated_count} 只\n"
            QMessageBox.information(self, "成功", message)
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"同步失败：{str(e)}")
    
    def update_stock_count(self):
        """更新股票计数"""
        self.stock_count_label.setText(f"共 {len(self.stocks)} 只股票")
    
    def remove_selected_stock(self):
        """移除选中的股票 - 支持多选（Ctrl/Shift）"""
        selected_rows = self.excel_table.selectionModel().selectedRows()
        
        if not selected_rows:
            QMessageBox.warning(self, "提示", "请先选择要移除的股票")
            return
        
        # 关键修复：排序后selectedRows返回的是视觉行号，需要转换为实际代码
        codes_to_remove = []
        for index in selected_rows:
            row = index.row()
            # 从第一列获取代码（格式：名称\n代码）
            item = self.excel_table.item(row, 0)
            if item:
                text = item.text()
                parts = text.split('\n')
                if len(parts) >= 2:
                    codes_to_remove.append(parts[1])  # 第二行是代码
        
        if not codes_to_remove:
            QMessageBox.warning(self, "提示", "无法获取选中股票的代码")
            return
        
        # 构建确认消息
        names_to_remove = []
        for code in codes_to_remove:
            for c, n in self.stocks:
                if c == code:
                    names_to_remove.append(f"{n}({code})")
                    break
        
        msg = f"确认移除以下 {len(names_to_remove)} 只股票？\n\n" + "\n".join(names_to_remove)
        reply = QMessageBox.question(self, "确认移除", msg,
                                     QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        
        removed_count = 0
        for code in codes_to_remove:
            # 从stocks中移除
            self.stocks = [(c, n) for c, n in self.stocks if c != code]
            # 同时从positions中移除
            if code in self.positions:
                del self.positions[code]
            # 清理已删除股票的widget引用，防止刷新时崩溃
            if code in self.excel_table._mini_charts:
                del self.excel_table._mini_charts[code]
            if code in self.stock_data:
                del self.stock_data[code]
            removed_count += 1
        
        if removed_count > 0:
            # 保存更新后的数据
            self.save_stocks()
            self.save_positions()
            
            # 刷新表格和计数
            self.refresh_table()
            self.update_stock_count()
            
            # 异步重启数据获取器（不阻塞UI）
            if self.data_fetcher:
                self.data_fetcher.stop()
                # 不在这里wait，让它自然结束
                self.start_data_fetcher()
            
            QMessageBox.information(
                self, 
                "成功", 
                f"已移除 {removed_count} 只股票"
            )
    
    def create_system_tray(self):
        """创建系统托盘"""
        self.tray_icon = QSystemTrayIcon(self)
        
        # 创建托盘菜单
        tray_menu = QMenu()
        
        restore_action = QAction("显示窗口", self)
        restore_action.triggered.connect(self.show)
        tray_menu.addAction(restore_action)
        
        refresh_action = QAction("刷新数据", self)
        refresh_action.triggered.connect(self.manual_refresh)
        tray_menu.addAction(refresh_action)
        
        tray_menu.addSeparator()
        
        # 导入持仓子菜单
        import_menu = QMenu("导入持仓", self)
        
        import_gtja_action = QAction("国泰君安持仓", self)
        import_gtja_action.triggered.connect(self.import_gtja_positions)
        import_menu.addAction(import_gtja_action)
        
        import_action = QAction("从文件导入", self)
        import_action.triggered.connect(self.import_from_csv)
        import_menu.addAction(import_action)
        
        tray_menu.addMenu(import_menu)
        
        tray_menu.addSeparator()
        
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.quit_application)
        tray_menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.setToolTip("A股监控工具")
        self.tray_icon.show()
        
        # 双击托盘图标显示窗口
        self.tray_icon.activated.connect(self.on_tray_activated)
    
    def import_gtja_positions(self):
        """导入国泰君安持仓"""
        # 显示登录对话框
        dialog = QDialog(self)
        dialog.setWindowTitle("国泰君安登录")
        dialog.setFixedSize(350, 200)
        
        layout = QVBoxLayout(dialog)
        
        # 用户名
        user_layout = QHBoxLayout()
        user_label = QLabel("账号:")
        user_label.setFixedWidth(80)
        user_input = QLineEdit()
        user_input.setPlaceholderText("请输入资金账号")
        user_layout.addWidget(user_label)
        user_layout.addWidget(user_input)
        layout.addLayout(user_layout)
        
        # 密码
        pwd_layout = QHBoxLayout()
        pwd_label = QLabel("密码:")
        pwd_label.setFixedWidth(80)
        pwd_input = QLineEdit()
        pwd_input.setEchoMode(QLineEdit.Password)
        pwd_input.setPlaceholderText("请输入交易密码")
        pwd_layout.addWidget(pwd_label)
        pwd_layout.addWidget(pwd_input)
        layout.addLayout(pwd_layout)
        
        # 提示信息
        hint_label = QLabel("提示: 需要国泰君安开放API权限")
        hint_label.setStyleSheet("color: #f44336; font-size: 9px;")
        layout.addWidget(hint_label)
        
        # 按钮
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        
        def do_login():
            username = user_input.text().strip()
            password = pwd_input.text().strip()
            
            if not username or not password:
                QMessageBox.warning(dialog, "错误", "请输入账号和密码")
                return
            
            # 连接并登录
            if self.position_manager.connect_brokerage('gtja'):
                if self.position_manager.login(username, password):
                    # 获取持仓
                    positions = self.position_manager.fetch_positions()
                    if positions:
                        # 将持仓股票添加到监控列表
                        position_stocks = self.position_manager.get_position_stocks()
                        for code, name in position_stocks:
                            if not any(c == code for c, _ in self.stocks):
                                self.stocks.append((code, name))
                        
                        self.save_stocks()
                        self.refresh_table()
                        self.update_stock_count()
                        
                        QMessageBox.information(self, "成功", 
                            f"已导入 {len(positions)} 条持仓记录\n其中 {len(position_stocks)} 只股票已添加到监控列表")
                        
                        # 重启数据获取器
                        if self.data_fetcher:
                            self.data_fetcher.stop()
                            self.data_fetcher.wait()
                            self.start_data_fetcher()
                        
                        dialog.accept()
                    else:
                        QMessageBox.warning(self, "提示", "未获取到持仓数据")
                else:
                    QMessageBox.critical(self, "错误", "登录失败，请检查账号密码或联系国泰君安申请API权限")
            else:
                QMessageBox.critical(self, "错误", "无法连接国泰君安API")
        
        buttons.accepted.connect(do_login)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        dialog.exec_()
    
    def on_tray_activated(self, reason):
        """托盘图标激活事件"""
        if reason == QSystemTrayIcon.DoubleClick:
            self.show()
    
    def minimize_to_tray(self):
        """最小化到系统托盘"""
        self.hide()
        self.tray_icon.showMessage("A股监控", "程序已在后台运行", QSystemTrayIcon.Information, 2000)
    
    def quit_application(self):
        """退出应用"""
        if self.data_fetcher:
            self.data_fetcher.stop()
        QApplication.quit()
    
    def closeEvent(self, event):
        """窗口关闭事件"""
        # 如果处于极简模式，不关闭极简面板，只最小化主窗口到托盘
        if self.is_minimalist_mode:
            event.ignore()
            # 不做任何操作，保持极简面板显示
            print("极简模式下忽略关闭事件")
            return
        
        # 正常模式下关闭极简模式窗口（如果存在）
        if self.minimalist_panel:
            self.minimalist_panel.close()
        if self.mini_float_button:
            self.mini_float_button.close()
        event.ignore()
        self.minimize_to_tray()

    def toggle_minimalist_mode(self):
        """切换极简模式"""
        try:
            if self.is_minimalist_mode:
                # 从极简模式切换回正常模式
                print(f"切换回正常模式: 极简面板={self.minimalist_panel}, 迷你按钮={self.mini_float_button}")
                self.hide_minimalist_panel()
                if self.mini_float_button:
                    self.mini_float_button.hide()
                # 确保主窗口可以显示
                if not self.isVisible():
                    self.show()
                self.is_minimalist_mode = False
                print("已切换回正常模式")
            else:
                # 切换到极简模式
                print("切换到极简模式")
                self.hide()
                self.show_minimalist_panel()
                self.is_minimalist_mode = True
                print("已进入极简模式")
        except Exception as e:
            import traceback
            print(f"切换极简模式失败: {e}")
            print(traceback.format_exc())
            # 恢复到安全状态
            self.is_minimalist_mode = False
            if not self.isVisible():
                self.show()

    def show_minimalist_panel(self):
        """显示极简模式面板"""
        try:
            if not self.minimalist_panel:
                self.minimalist_panel = MinimalistPanel()  # 不传parent，作为独立窗口
                # 设置按钮回调
                self.minimalist_panel.set_mini_button_callback(self.show_mini_button)
                self.minimalist_panel.set_max_button_callback(self.restore_to_normal_mode)

            # 如果数据还没有获取到，后台触发一次获取（不阻塞UI）
            if not self.stock_data:
                print("数据为空，后台触发一次数据获取")
                def _fetch_and_emit_async():
                    try:
                        self.data_fetcher.fetch_and_emit()
                    except Exception as e:
                        print(f"后台获取数据失败: {e}")
                threading.Thread(target=_fetch_and_emit_async, daemon=True).start()
            
            # 更新股票数据
            self.minimalist_panel.update_stocks(self.stocks, self.stock_data, self.positions)

            # 设置位置（屏幕右下角）
            screen_geo = QApplication.desktop().screenGeometry()
            x = screen_geo.width() - self.minimalist_panel.width() - 50
            y = screen_geo.height() - self.minimalist_panel.height() - 100
            self.minimalist_panel.move(max(0, x), max(0, y))
            self.minimalist_panel.show()
            self.minimalist_panel.raise_()  # 确保窗口在最前面
            print("极简面板已显示")
        except Exception as e:
            import traceback
            print(f"显示极简面板失败: {e}")
            print(traceback.format_exc())
            # 如果极简面板显示失败，显示错误提示并返回正常模式
            self.is_minimalist_mode = False
            self.show()
            QMessageBox.warning(self, "错误", f"无法进入极简模式: {str(e)}")

    def hide_minimalist_panel(self):
        """隐藏极简模式面板"""
        if self.minimalist_panel:
            self.minimalist_panel.hide()

    def show_mini_button(self):
        """显示迷你浮动按钮"""
        try:
            if not self.mini_float_button:
                self.mini_float_button = MiniFloatButton()  # 不传parent，作为独立窗口
                self.mini_float_button.set_click_callback(self.restore_from_mini_button)

            # 设置位置（屏幕右下角）
            screen_geo = QApplication.desktop().screenGeometry()
            x = screen_geo.width() - self.mini_float_button.width() - 20
            y = screen_geo.height() - self.mini_float_button.height() - 50
            self.mini_float_button.move(max(0, x), max(0, y))

            # 隐藏极简面板，显示迷你按钮
            self.hide_minimalist_panel()
            self.mini_float_button.show()
            print("迷你按钮已显示")
        except Exception as e:
            import traceback
            print(f"显示迷你按钮失败: {e}")
            print(traceback.format_exc())
            # 失败则显示极简面板
            self.show_minimalist_panel()

    def restore_from_mini_button(self):
        """从迷你按钮恢复极简面板"""
        if self.mini_float_button:
            self.mini_float_button.hide()
        self.show_minimalist_panel()

    def restore_to_normal_mode(self):
        """从极简模式恢复到正常模式"""
        try:
            self.hide_minimalist_panel()
            if self.mini_float_button:
                self.mini_float_button.hide()
            self.show()
            self.is_minimalist_mode = False
            print("已恢复正常模式")
        except Exception as e:
            import traceback
            print(f"恢复正常模式失败: {e}")
            print(traceback.format_exc())

    def edit_position_data(self, code, name, field, current_value):
        """编辑持仓数据（持有数或成本）"""
        from PyQt5.QtGui import QIntValidator, QDoubleValidator

        dialog = QDialog(self)
        dialog.setWindowTitle("编辑持仓")
        dialog.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint)
        dialog.setFixedSize(320, 150)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(12)

        # 标题
        title_label = QLabel(f"{name} ({code})")
        title_label.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        title_label.setStyleSheet("color: #333;")
        layout.addWidget(title_label)

        # 输入框 - 使用 QLineEdit 替代 QSpinBox，避免箭头按钮占用空间
        input_layout = QHBoxLayout()
        input_layout.setSpacing(10)
        field_name = "持有数" if field == 'quantity' else "成本价"
        field_label = QLabel(f"{field_name}:")
        field_label.setFixedWidth(55)
        field_label.setFont(QFont("Microsoft YaHei", 10))
        input_layout.addWidget(field_label)

        input_widget = QLineEdit()
        input_widget.setFixedWidth(220)
        input_widget.setFont(QFont("Consolas", 16))
        input_widget.setStyleSheet("""
            QLineEdit {
                border: 1px solid #ccc;
                border-radius: 3px;
                padding: 6px 10px;
            }
            QLineEdit:focus {
                border-color: #4a90d9;
            }
        """)

        if field == 'quantity':
            input_widget.setValidator(QIntValidator(0, 10000000))
            input_widget.setText(str(int(current_value)))
        else:  # cost_price
            input_widget.setValidator(QDoubleValidator(0, 99999.999, 3))
            input_widget.setText(f"{float(current_value):.3f}")

        input_layout.addWidget(input_widget)
        layout.addLayout(input_layout)

        layout.addStretch()

        # 按钮
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.setCenterButtons(True)

        def on_accept():
            text = input_widget.text().strip()
            if not text:
                return
            try:
                if field == 'quantity':
                    new_value = int(text)
                else:
                    new_value = float(text)
            except ValueError:
                return

            # 确保 code 在 positions 中
            if code not in self.positions:
                self.positions[code] = {
                    'name': name,
                    'quantity': 0,
                    'cost_price': 0.0,
                    'market_value': 0.0,
                    'total_profit': 0.0,
                    'total_profit_percent': 0.0,
                    'today_profit': 0.0,
                    'today_profit_percent': 0.0,
                    'current_price': 0.0
                }

            # 更新持仓数据
            self.positions[code][field] = new_value
            quantity = self.positions[code].get('quantity', 0)
            cost_price = self.positions[code].get('cost_price', 0)
            current_price = self.positions[code].get('current_price', 0)

            # 尝试从实时数据获取当前价格
            if code in self.stock_data:
                realtime_price = self.stock_data[code].get('current_price', 0)
                if realtime_price > 0:
                    current_price = realtime_price
                    self.positions[code]['current_price'] = current_price

            # 只要有数量就计算市值（不要求current_price>0）
            if quantity > 0:
                if current_price > 0:
                    market_value = quantity * current_price
                    self.positions[code]['market_value'] = market_value
                    if cost_price > 0:
                        profit_loss = (current_price - cost_price) * quantity
                        profit_percent = (profit_loss / (cost_price * quantity)) * 100
                        self.positions[code]['total_profit'] = profit_loss
                        self.positions[code]['total_profit_percent'] = profit_percent
                
                # 计算当日盈亏（基于当日涨跌额）
                change = 0
                if code in self.stock_data:
                    change = self.stock_data[code].get('change', 0)
                    change_percent = self.stock_data[code].get('change_percent', 0)
                else:
                    change_percent = self.positions[code].get('change_percent', 0)
                
                today_profit = change * quantity
                self.positions[code]['today_profit'] = today_profit
                self.positions[code]['today_profit_percent'] = change_percent

            print(f"编辑持仓: {code} {field}={new_value}, quantity={quantity}, cost={cost_price}, price={current_price}, today_profit={today_profit}")
            self.save_positions()
            self.refresh_table()
            dialog.accept()

        buttons.accepted.connect(on_accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        dialog.exec_()

    def on_data_updated(self, data):
        """数据更新回调 - 使用统一的 compute_position_profit"""
        # 检查是否是新的交易日，如果是则清除 is_today_added 标记和 is_sold 标记的股票
        today_str = datetime.now().strftime('%Y-%m-%d')
        sold_codes_cleared = False
        if not hasattr(self, '_last_trading_date') or self._last_trading_date != today_str:
            self._last_trading_date = today_str
            # 清除 is_today_added 标记
            for code in list(self.positions.keys()):
                if self.positions[code].get('is_today_added', False):
                    self.positions[code]['is_today_added'] = False
            # 清除 is_sold 标记的股票（第二日打开程序时剔除）
            sold_codes = [code for code in self.positions if self.positions[code].get('is_sold', False)]
            for code in sold_codes:
                self.stocks = [(c, n) for c, n in self.stocks if c != code]
                if code in self.positions:
                    del self.positions[code]
                if code in self.excel_table._mini_charts:
                    del self.excel_table._mini_charts[code]
            if sold_codes:
                sold_codes_cleared = True
                self.save_stocks()
                self.save_positions()

        self.stock_data.update(data)

        # 对每只有实时数据的股票进行统一计算（注意：不覆盖已卖出股票的盈亏数据）
        for code, info in data.items():
            if code not in self.positions:
                continue
            position = self.positions[code]
            # 已卖出的股票不重新计算（盈亏已锁定）
            if position.get('is_sold', False):
                continue
            
            try:
                p = compute_position_profit(code, position, info)
                # 保存计算结果
                position['change_percent'] = p['change_percent']
                position['current_price'] = p['current_price']
                rt_price = info.get('current_price', 0)
                rt_change = info.get('change', 0)
                if rt_price > 0 and rt_change != 0:
                    position['prev_close'] = rt_price - rt_change
                position['market_value'] = p['market_value']
                position['total_profit'] = p['total_profit']
                position['total_profit_percent'] = p['total_profit_percent']
                position['today_profit'] = p['today_profit']
                position['today_profit_percent'] = p['today_profit_percent']
            except Exception as e:
                continue  # 单个股票计算失败不影响其他股票

        if sold_codes_cleared:
            # 清除了is_sold股票后需要重启数据获取器
            if self.data_fetcher:
                self.data_fetcher.stop()
            self.start_data_fetcher()

        self.refresh_table()  # refresh_table会调用update_total_summary

        # 更新极简模式面板（如果处于极简模式）
        if self.is_minimalist_mode and self.minimalist_panel:
            self.minimalist_panel.update_stocks(self.stocks, self.stock_data, self.positions)

        # 更新时间戳
        now = datetime.now().strftime("%H:%M:%S")
        self.last_update_label.setText(f"最后更新: {now}")

        # 更新走势图（如果当前在走势标签页）
        if self.tab_widget.currentIndex() == 1 and self.stocks:
            # 使用当前显示的股票，没有则用第一只
            target_code = self.current_chart_stock
            if not target_code or target_code not in data:
                target_code = self.stocks[0][0]
            if target_code in data:
                # 查找股票名称
                target_name = ""
                for code, name in self.stocks:
                    if code == target_code:
                        target_name = name
                        break
                self.chart_widget.stock_code = target_code
                self.chart_widget.stock_name = target_name
                # 优先使用缓存的分时数据，避免阻塞UI
                real_data = data.get(target_code, None)
                cached = self._minute_data_cache.get(target_code)
                if cached:
                    self.chart_widget.set_minute_data(cached, real_data)
                else:
                    # 后台获取
                    def _fetch_chart_data(code=target_code, name=target_name, rd=real_data):
                        md = self._generate_simulated_minute_data(code, name)
                        if md:
                            self._minute_data_cache[code] = md
                            QTimer.singleShot(0, lambda: self.chart_widget.set_minute_data(md, rd))
                    threading.Thread(target=_fetch_chart_data, daemon=True).start()


def main():
    app = QApplication(sys.argv)
    
    # 设置应用属性
    app.setApplicationName("Stock Monitor")
    app.setOrganizationName("StealthStock")

    # 全局样式：增大所有录入控件和标签的字体
    app.setStyleSheet("""
        QLineEdit {
            font-size: 13px;
            font-family: "Microsoft YaHei";
            padding: 4px 6px;
        }
        QSpinBox, QDoubleSpinBox {
            font-size: 13px;
            font-family: "Microsoft YaHei";
            padding: 3px 5px;
        }
        QComboBox {
            font-size: 13px;
            font-family: "Microsoft YaHei";
            padding: 3px 5px;
        }
        QLabel {
            font-size: 12px;
            font-family: "Microsoft YaHei";
        }
    """)
    
    # 创建主窗口
    window = StealthStockMonitor()
    window.show()
    
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
