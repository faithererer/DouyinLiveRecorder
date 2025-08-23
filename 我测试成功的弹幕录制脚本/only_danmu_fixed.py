#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抖音直播弹幕录制单文件Demo - 修复版本
基于DouyinLiveRecorder项目完整实现

使用方法:
1. 安装依赖: pip install requests websocket-client protobuf jsengine
2. 修改下面的配置参数
3. 运行: python only_danmu_fixed.py

注意: 此版本使用原项目的完整实现逻辑
"""

import _thread
import gzip
import hashlib
import json
import os
import random
import time
import traceback
from urllib.parse import urlparse, parse_qs, urlunparse, urlencode

import jsengine
import requests
import websocket
from google.protobuf import json_format

# 导入原项目的模块
from dylr.core.dy_pb2 import PushFrame, Response, ChatMessage
from dylr.util import cookie_utils

# ==================== 配置区域 ====================
id = 66186758468
name = "千年"
output_path = f"./danmu/{name}"

# ==================== 工具函数 ====================

def get_random_ua():
    """获取随机User-Agent"""
    os_list = ['(Windows NT 10.0; WOW64)', '(Windows NT 10.0; Win64; x64)',
               '(Windows NT 6.3; WOW64)', '(Windows NT 6.1; Win64; x64)',
               '(X11; Linux x86_64)', '(Macintosh; Intel Mac OS X 10_12_6)']
    chrome_version_list = ['110.0.5481.77', '109.0.5414.74', '108.0.5359.71',
                          '107.0.5304.62', '106.0.5249.61', '105.0.5195.52']
    return f"Mozilla/5.0 {random.choice(os_list)} AppleWebKit/537.36 (KHTML, like Gecko) " \
           f"Chrome/{random.choice(chrome_version_list)} Safari/537.36"

def get_request_headers():
    """获取请求头"""
    return {
        'user-agent': get_random_ua(),
        'cookie': cookie_utils.cookie_cache or get_cookie()
    }

def get_cookie():
    """获取cookie"""
    if cookie_utils.cookie_cache:
        return cookie_utils.cookie_cache
    
    # 自动获取cookie
    cookie_utils.auto_get_cookie()
    return cookie_utils.cookie_cache or '__ac_nonce=0638733a400869171be51'

def get_api_url(room_id):
    """获取API URL"""
    return 'https://live.douyin.com/webcast/room/web/enter/?aid=6383&live_id=1&device_platform=web&language=zh-CN' \
           '&enter_from=web_live&cookie_enabled=true&screen_width=1920&screen_height=1080&browser_language=zh-CN' \
           f'&browser_platform=Win32&browser_name=Chrome&browser_version=109.0.0.0&web_rid={room_id}' \
           f'&enter_source=&Room-Enter-User-Login-Ab=1&is_need_double_stream=false&a_bogus=0'

def get_live_state_json(room_id):
    """获取直播状态信息"""
    api_url = get_api_url(room_id)
    try:
        req = requests.get(api_url, headers=get_request_headers(), timeout=10)
        res = req.text
        if '系统繁忙，请稍后再试' in res:
            print('系统繁忙，请稍后再试')
            cookie_utils.record_cookie_failed()
            return None
        
        info_json = json.loads(res)
        info_json = info_json['data']['data'][0]
        return info_json
    except Exception as e:
        print(f'获取直播状态失败: {e}')
        cookie_utils.record_cookie_failed()
        return None

def get_ms_stub(live_room_real_id, user_unique_id):
    """生成签名参数"""
    params = {
        "live_id": "1",
        "aid": "6383",
        "version_code": 180800,
        "webcast_sdk_version": '1.0.14-beta.0',
        "room_id": live_room_real_id,
        "sub_room_id": "",
        "sub_channel_id": "",
        "did_rule": "3",
        "user_unique_id": user_unique_id,
        "device_platform": "web",
        "device_type": "",
        "ac": "",
        "identity": "audience"
    }
    sig_params = ','.join([f'{k}={v}' for k, v in params.items()])
    return hashlib.md5(sig_params.encode()).hexdigest()

def build_request_url(url: str, user_agent: str) -> str:
    """构建请求URL"""
    parsed_url = urlparse(url)
    existing_params = parse_qs(parsed_url.query)
    existing_params['aid'] = ['6383']
    existing_params['device_platform'] = ['web']
    existing_params['browser_language'] = ['zh-CN']
    existing_params['browser_platform'] = ['Win32']
    existing_params['browser_name'] = ['Chrome']
    existing_params['browser_version'] = ['109.0.0.0']
    new_query_string = urlencode(existing_params, doseq=True)
    new_url = urlunparse((
        parsed_url.scheme,
        parsed_url.netloc,
        parsed_url.path,
        parsed_url.params,
        new_query_string,
        parsed_url.fragment
    ))
    return new_url

def get_danmu_ws_url(room_id, live_room_real_id):
    """获取弹幕WebSocket URL - 使用原项目的完整实现"""
    user_unique_id = random.randint(7300000000000000000, 7999999999999999999)
    
    # 使用原项目的JS引擎生成签名
    try:
        with open(r'dylr/util/webmssdk.js', 'r', encoding='utf-8') as f:
            js_enc = f.read()
        
        ua = get_request_headers()['user-agent']
        
        ctx = jsengine.jsengine()
        js_dom = f"""
document = {{}}
window = {{}}
navigator = {{
  'userAgent': '{ua}'
}}
""".strip()
        final_js = js_dom + js_enc
        ctx.eval(final_js)
        
        from dylr.util.url_utils import get_ms_stub
        function_caller = f"get_sign('{get_ms_stub(live_room_real_id, user_unique_id)}')"
        signature = ctx.eval(function_caller)
        
        webcast5_params = {
            "room_id": live_room_real_id,
            "compress": 'gzip',
            "version_code": 180800,
            "webcast_sdk_version": '1.0.14-beta.0',
            "live_id": "1",
            "did_rule": "3",
            "user_unique_id": user_unique_id,
            "identity": "audience",
            "signature": signature,
        }
        
        uri = build_request_url(
            f"wss://webcast5-ws-web-lf.douyin.com/webcast/im/push/v2/?{'&'.join([f'{k}={v}' for k, v in webcast5_params.items()])}",
            ua)
        
        return uri
        
    except Exception as e:
        print(f'生成WebSocket URL失败: {e}')
        # 使用备用方案
        current_time = int(time.time())
        ws_url = (
            f"wss://webcast3-ws-web-lf.douyin.com/webcast/im/push/v2/"
            f"?app_name=douyin_web"
            f"&version_code=180800"
            f"&webcast_sdk_version=1.3.0"
            f"&update_version_code=1.3.0"
            f"&compress=gzip"
            f"&internal_ext=internal_src:dim|wss_push_room_id:{live_room_real_id}|wss_push_did:{user_unique_id}|dim_log_id:2023011316221327ACACF0E44A2C0E8200|fetch_time:{current_time}123|seq:1|wss_info:0-1673598133900-0-0|wrds_kvs:WebcastRoomRankMessage-1673597852921055645_WebcastRoomStatsMessage-1673598128993068211"
            f"&cursor=u-1_h-1_t-1672732684536_r-1_d-1"
            f"&host=https://live.douyin.com"
            f"&aid=6383"
            f"&live_id=1"
            f"&did_rule=3"
            f"&debug=false"
            f"&endpoint=live_pc"
            f"&support_wrds=1"
            f"&im_path=/webcast/im/fetch/"
            f"&device_platform=web"
            f"&cookie_enabled=true"
            f"&screen_width=1920"
            f"&screen_height=1080"
            f"&browser_language=zh-CN"
            f"&browser_platform=Win32"
            f"&browser_name=Chrome"
            f"&browser_version=109.0.0.0"
            f"&browser_online=true"
            f"&tz_name=Asia/Shanghai"
            f"&identity=audience"
            f"&room_id={live_room_real_id}"
            f"&heartbeatDuration=0"
            f"&signature=00000000"
        )
        return ws_url

# ==================== 弹幕录制器 ====================

class DanmuRecorder:
    def __init__(self, room_id, room_name):
        self.room_id = room_id
        self.room_name = room_name
        self.room_real_id = None
        self.start_time = None
        self.ws = None
        self.stop_signal = False
        self.danmu_amount = 0
        self.last_danmu_time = 0
        self.filename = None
        self.retry = 0
        
    def start(self):
        """开始录制弹幕"""
        print(f'开始录制 {self.room_name}({self.room_id}) 的弹幕')
        
        # 获取直播间信息
        print('正在获取直播间信息...')
        room_json = get_live_state_json(self.room_id)
        if room_json is None:
            print('无法获取直播间信息，请检查房间ID是否正确')
            return
            
        self.room_real_id = room_json.get('id_str', str(self.room_id))
        print(f'真实房间ID: {self.room_real_id}')
        
        # 检查是否在直播
        status = room_json.get('status', 0)
        if status != 2:
            print(f'主播未在直播 (状态: {status})')
            return
            
        print('主播正在直播，开始连接弹幕服务器...')
        
        if self.start_time is None:
            self.start_time = time.localtime()
        self.start_time_t = int(time.mktime(self.start_time))
        
        # 创建输出目录
        if not os.path.exists(output_path):
            os.makedirs(output_path)
            print(f'创建输出目录: {output_path}')
            
        # 创建弹幕文件
        start_time_str = time.strftime('%Y%m%d_%H%M%S', self.start_time)
        self.filename = f"{output_path}/{start_time_str}.xml"
        
        # 写入XML头部
        with open(self.filename, 'w', encoding='UTF-8') as file:
            file.write("<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
                      "<?xml-stylesheet type=\"text/xsl\" href=\"#s\"?>\n"
                      "<i>\n")
        
        print(f'弹幕文件: {self.filename}')
        
        # 创建WebSocket连接
        try:
            ws_url = get_danmu_ws_url(self.room_id, self.room_real_id)
            print(f'连接弹幕服务器...')
            
            self.ws = websocket.WebSocketApp(
                url=ws_url,
                header=get_request_headers(),
                cookie=cookie_utils.cookie_cache,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
                on_open=self._on_open,
            )
            self.ws.run_forever()
        except Exception as e:
            print(f'连接失败: {e}')
            
    def stop(self):
        """停止录制"""
        self.stop_signal = True
        if self.ws:
            self.ws.close()
            
    def _on_open(self, ws):
        """WebSocket连接打开"""
        print('弹幕连接已建立，开始接收弹幕...')
        print('按 Ctrl+C 停止录制')
        print('-' * 50)
        _thread.start_new_thread(self._heartbeat, (ws,))
        
    def _on_message(self, ws, message):
        """处理弹幕消息 - 使用原项目的完整实现"""
        try:
            wssPackage = PushFrame()
            wssPackage.ParseFromString(message)
            logid = wssPackage.logid
            decompressed = gzip.decompress(wssPackage.payload)
            payloadPackage = Response()
            payloadPackage.ParseFromString(decompressed)

            # 发送ack包
            if payloadPackage.needAck:
                obj = PushFrame()
                obj.payloadType = 'ack'
                obj.logid = logid
                obj.payloadType = payloadPackage.internalExt
                data = obj.SerializeToString()
                ws.send(data, websocket.ABNF.OPCODE_BINARY)
                
            # 处理消息
            for msg in payloadPackage.messagesList:
                if msg.method == 'WebcastChatMessage':
                    chatMessage = ChatMessage()
                    chatMessage.ParseFromString(msg.payload)
                    data = json_format.MessageToDict(chatMessage, preserving_proto_field_name=True)
                    now = time.time()
                    second = now - self.start_time_t
                    self.danmu_amount += 1
                    self.last_danmu_time = now
                    user = data['user']['nickName']
                    content = data['content']
                    
                    # 写入弹幕到文件
                    with open(self.filename, 'a', encoding='UTF-8') as file:
                        file.write(f"  <d p=\"{round(second, 2)},1,25,16777215,"
                                  f"{int(now * 1000)},0,1602022773,0\" user=\"{user}\">{content}</d>\n")
                    
                    print(f'[{time.strftime("%H:%M:%S")}] {user}: {content}')
                    
        except Exception as e:
            print(f'处理弹幕消息失败: {e}')
            
    def _on_error(self, ws, error):
        """WebSocket错误处理"""
        print(f'弹幕连接错误: {error}')
        
    def _on_close(self, ws, close_status_code, close_msg):
        """WebSocket连接关闭"""
        print('-' * 50)
        print('弹幕连接已关闭')
        
        # 写入XML尾部
        if self.filename and os.path.exists(self.filename):
            with open(self.filename, 'a', encoding='UTF-8') as file:
                file.write('</i>')
        
        print(f'弹幕录制结束，共录制 {self.danmu_amount} 条弹幕')
        print(f'文件保存至: {self.filename}')
        
        # 重连逻辑
        if not self.stop_signal and self.retry < 3:
            self.retry += 1
            print(f'尝试重连 ({self.retry}/3)...')
            time.sleep(2)
            self.start()
        
    def _heartbeat(self, ws):
        """心跳包"""
        t = 9
        while True:
            if self.stop_signal:
                ws.close()
                break
            if not ws.keep_running:
                break
                
            if t % 10 == 0:
                # 发送心跳包
                try:
                    obj = PushFrame()
                    obj.payloadType = 'hb'
                    data = obj.SerializeToString()
                    ws.send(data, websocket.ABNF.OPCODE_BINARY)
                except:
                    pass
                    
                # 检查是否长时间没有弹幕
                now = time.time()
                if t > 30 and now - self.last_danmu_time > 60:
                    print('长时间无弹幕，检查主播是否下播...')
                    # 这里可以添加检查主播状态的逻辑
                    
            t += 1
            time.sleep(1)

# ==================== 主程序 ====================

def main():
    """主程序入口"""
    print("=" * 60)
    print("抖音直播弹幕录制Demo - 修复版本")
    print("基于DouyinLiveRecorder项目完整实现")
    print("=" * 60)
    print(f"房间ID: {id}")
    print(f"主播名: {name}")
    print(f"输出路径: {output_path}")
    print("=" * 60)
    print("注意: 此版本使用原项目的完整实现逻辑")
    print("=" * 60)
    
    # 初始化cookie
    if not cookie_utils.cookie_cache:
        print('正在获取cookie...')
        cookie_utils.auto_get_cookie()
    
    # 创建弹幕录制器
    recorder = DanmuRecorder(id, name)
    
    try:
        # 开始录制
        recorder.start()
    except KeyboardInterrupt:
        print("\n用户中断录制")
        recorder.stop()
    except Exception as e:
        print(f"录制过程中发生错误: {e}")
        traceback.print_exc()
    finally:
        print("程序结束")

if __name__ == "__main__":
    main()