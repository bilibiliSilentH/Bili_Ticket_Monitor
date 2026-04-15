"""
Bili_Ticket_Monitor
"""

import time
import threading
from datetime import datetime
from typing import List, Tuple, Optional
import requests
from urllib3 import disable_warnings
from urllib3.exceptions import InsecureRequestWarning
from colorama import Fore, Style, init
from tabulate import tabulate
from wcwidth import wcswidth

disable_warnings(InsecureRequestWarning)
requests.packages.urllib3.disable_warnings()
init(autoreset=True)


class Config:
    TIMEOUT = 50
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"
    }

    def __init__(self, ticket_id: str, refresh_interval: float):
        self.TICKET_ID = ticket_id
        self.API_URL = f"https://show.bilibili.com/api/ticket/project/getV2?version=134&id={ticket_id}"
        self.REFRESH_INTERVAL = refresh_interval

class StatusColor:
    COLOR_MAP = {
        "已售罄": Fore.RED, "已停售": Fore.RED, "不可售": Fore.RED,
        "未开售": Fore.RED, "暂时售罄": Fore.YELLOW, "预售中": Fore.GREEN
    }
    DEFAULT = Fore.WHITE


def clear_screen():
    print("\033c", end="")


def process_data(json_data: dict) -> Tuple[Optional[str], Optional[List[List[str]]]]:
    """
    解析API并返回元组(活动名称, 票种列表）
    """
    data = json_data.get('data', {})
    if not data:
        return None, None

    name = data.get('name', '')
    tickets = [
        [f"{t.get('screen_name', '')} {t.get('desc', '')}", t.get('sale_flag', {}).get('display_name', '')]
        for screen in data.get('screen_list', [])
        for t in screen.get('ticket_list', [])
    ]
    return name, tickets or None


def validate_ticket_id(ticket_id: str) -> bool:
    """
    验证票务ID格式是否合法（非空且为纯数字）
    """
    return ticket_id.strip().isdigit()


def input_ticket_id() -> str:
    """
    提示用户输入票务ID，验证后返回
    """
    while True:
        print(f"{Fore.CYAN}请输入票务ID（纯数字，例如: 115413）")
        ticket_id = input(f"{Fore.WHITE}票务ID > ").strip()
        if validate_ticket_id(ticket_id):
            return ticket_id
        print(f"{Fore.RED}输入无效，票务ID应为纯数字，请重新输入。\n")


def input_refresh_interval() -> float:
    """
    提示用户输入刷新间隔，验证后返回
    """
    while True:
        print(f"{Fore.CYAN}请输入刷新时间间隔（单位：秒，最小值0.1，直接回车默认1秒，触发风控概不负责）：")
        raw = input(f"{Fore.WHITE}刷新间隔 > ").strip()
        if raw == "":
            return 1.0
        try:
            interval = float(raw)
            if interval >= 0.1:
                return interval
            print(f"{Fore.RED}刷新间隔不能小于0.1秒，请重新输入。\n")
        except ValueError:
            print(f"{Fore.RED}输入无效，请输入数字。\n")


class Monitor:
    def __init__(self, config: Config):
        self.config = config
        self.stop = threading.Event()   # Ctrl + C以退出程序
        # 移除pause_event，个人习惯使用线程锁
        self.lock = threading.Lock()
        self.last_data = None
        self.current_name = ""
        self.healthy = True

    def start(self):
        # 启动时先清屏一次
        clear_screen()

        time_thread = threading.Thread(target=self.show_time, daemon=True)
        monitor_thread = threading.Thread(target=self.run_monitor, daemon=True)

        time_thread.start()
        monitor_thread.start()

        try:
            while time_thread.is_alive() or monitor_thread.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop.set()

    def show_time(self):
        """
        UI渲染线程
        """
        while not self.stop.is_set():
            if self.last_data and self.healthy:
                new_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                with self.lock:
                    # \033[H：移动光标到左上角，覆盖式刷新，避免 clear_screen 导致的闪烁
                    print("\033[H", end="")
                    print(
                        f"{Fore.YELLOW}监控ID: {self.config.TICKET_ID} | 刷新间隔: {self.config.REFRESH_INTERVAL}s\033[K")
                    print("=" * 40 + "\033[K")
                    print(f"{Fore.GREEN}当前时间: {new_time}\033[K")

                    # 打印表格
                    self._print_table(self.current_name, self.last_data)

                    # \033[J：清除屏幕下方可能残留的旧字符，当票种减少时避免内容重叠
                    print("\033[J", end="", flush=True)
            time.sleep(1)

    def _print_table(self, name: str, tickets: List[List[str]]):
        """
        内部方法：格式化并打印表格
        """
        col1 = max(wcswidth(row[0]) for row in tickets)
        col2 = max(len(row[1]) for row in tickets)

        print(f"\n{Style.BRIGHT}{name}\033[K")
        print(f"{Fore.CYAN}{'票种'.ljust(col1)}{'状态'.rjust(col2)}\033[K")
        print('-' * (col1 + col2 + 8) + "\033[K")

        formatted = [
            [row[0].ljust(col1),
             f"{StatusColor.COLOR_MAP.get(row[1], StatusColor.DEFAULT)}{row[1]}{Style.RESET_ALL}"]
            for row in tickets
        ]
        print(tabulate(formatted, tablefmt='plain'))

    def run_monitor(self):
        """
        数据请求线程
        """
        with requests.Session() as s:
            s.verify = False
            s.mount('https://', requests.adapters.HTTPAdapter(max_retries=3))

            while not self.stop.is_set():
                try:
                    resp = s.get(self.config.API_URL, headers=self.config.HEADERS, timeout=self.config.TIMEOUT)
                    resp.raise_for_status()

                    name, tickets = process_data(resp.json())
                    if not tickets:
                        time.sleep(self.config.REFRESH_INTERVAL)
                        continue

                    with self.lock:
                        self.current_name = name
                        self.last_data = tickets
                        self.healthy = True

                except requests.exceptions.HTTPError as e:
                    self.handle_error("HTTP错误" if e.response.status_code != 412 else "触发风控！立即停止！",
                                      e.response.status_code == 412)
                except requests.exceptions.RequestException as e:
                    self.handle_error(f"请求异常: {e}", False)

                time.sleep(self.config.REFRESH_INTERVAL)

    def handle_error(self, msg, critical):
        with self.lock:
            print(Fore.RED + f"\n{msg}")
            self.healthy = False
        if critical:
            self.stop.set()


if __name__ == "__main__":
    clear_screen()
    print(f"{Style.BRIGHT}{Fore.YELLOW}bilibili票务监控工具")
    print("=" * 40)

    ticket_id = input_ticket_id()
    print()
    refresh_interval = input_refresh_interval()
    config = Config(ticket_id, refresh_interval)

    clear_screen()
    print(f"{Fore.YELLOW}监控ID: {config.TICKET_ID} | 刷新间隔: {config.REFRESH_INTERVAL}s")
    print("=" * 40)

    Monitor(config).start()
    input("\n按回车键退出程序...\n")
