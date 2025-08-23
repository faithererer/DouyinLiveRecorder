
# -*- encoding: utf-8 -*-

"""
Date: 2025-08-23
Function: 抖音直播弹幕录制模块
"""

import _thread
import gzip
import hashlib
import json
import os
import random
import time
import traceback
import threading
from urllib.parse import urlparse, parse_qs, urlunparse, urlencode
from typing import Optional, Dict, Any
from pathlib import Path

import requests
import websocket
import jsengine
from google.protobuf import json_format

from .utils import logger
from .http_clients.async_http import async_req
from .douyin_protobuf import parse_danmu_message, create_ack_frame, create_heartbeat_frame


class DouyinDanmuRecorder:
    """抖音弹幕录制器"""
    
    def __init__(self, room_id: str, room_name: str, output_dir: str, 
                 video_filename: str = None, cookies: str = None):
        """
        初始化弹幕录制器
        
        Args:
            room_id: 房间ID
            room_name: 主播名称
            output_dir: 输出目录
            video_filename: 视频文件名（用于保持一致性）
            cookies: Cookie字符串
        """
        self.room_id = room_id
        self.room_name = room_name
        self.room_real_id = None
        self.output_dir = Path(output_dir)
        self.video_filename = video_filename
        self.cookies = cookies
        
        self.start_time = None
        self.start_time_t = None
        self.ws = None
        self.stop_signal = False
        self.danmu_amount = 0
        self.last_danmu_time = 0
        self.filename = None
        self.retry = 0
        self.max_retry = 3
        
        # 分段相关
        self.segment_index = 0
        self.segment_start_time = None
        self.current_segment_file = None
        
        # 线程锁
        self.file_lock = threading.Lock()
        
        # 确保输出目录存在
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
    def get_random_ua(self) -> str:
        """获取随机User-Agent"""
        os_list = ['(Windows NT 10.0; WOW64)', '(Windows NT 10.0; Win64; x64)',
                   '(Windows NT 6.3; WOW64)', '(Windows NT 6.1; Win64; x64)',
                   '(X11; Linux x86_64)', '(Macintosh; Intel Mac OS X 10_12_6)']
        chrome_version_list = ['110.0.5481.77', '109.0.5414.74', '108.0.5359.71',
                              '107.0.5304.62', '106.0.5249.61', '105.0.5195.52']
        return f"Mozilla/5.0 {random.choice(os_list)} AppleWebKit/537.36 (KHTML, like Gecko) " \
               f"Chrome/{random.choice(chrome_version_list)} Safari/537.36"
    
    def get_request_headers(self) -> Dict[str, str]:
        """获取请求头"""
        headers = {
            'user-agent': self.get_random_ua(),
            'referer': 'https://live.douyin.com/',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
        }
        if self.cookies:
            headers['cookie'] = self.cookies
        return headers
    
    async def get_live_state_json(self) -> Optional[Dict[str, Any]]:
        """获取直播状态信息"""
        api_url = (f'https://live.douyin.com/webcast/room/web/enter/?aid=6383&live_id=1'
                  f'&device_platform=web&language=zh-CN&enter_from=web_live'
                  f'&cookie_enabled=true&screen_width=1920&screen_height=1080'
                  f'&browser_language=zh-CN&browser_platform=Win32&browser_name=Chrome'
                  f'&browser_version=109.0.0.0&web_rid={self.room_id}'
                  f'&enter_source=&Room-Enter-User-Login-Ab=1&is_need_double_stream=false&a_bogus=0')
        
        try:
            response = await async_req(url=api_url, headers=self.get_request_headers())
            if '系统繁忙，请稍后再试' in response:
                logger.warning('系统繁忙，请稍后再试')
                return None
            
            info_json = json.loads(response)
            logger.debug(f'API响应结构: {list(info_json.keys())}')
            
            # 检查响应结构
            if 'data' not in info_json:
                logger.error(f'API响应中缺少data字段，响应内容: {info_json}')
                return None
            
            if 'data' not in info_json['data']:
                logger.error(f'API响应data字段中缺少data子字段，data内容: {info_json["data"]}')
                return None
            
            if not isinstance(info_json['data']['data'], list) or len(info_json['data']['data']) == 0:
                logger.error(f'API响应data.data不是非空列表，内容: {info_json["data"]["data"]}')
                return None
            
            return info_json['data']['data'][0]
        except json.JSONDecodeError as e:
            logger.error(f'JSON解析失败: {e}，响应内容: {response[:500]}...')
            return None
        except Exception as e:
            logger.error(f'获取直播状态失败: {e}')
            return None
    
    def get_ms_stub(self, live_room_real_id: str, user_unique_id: str) -> str:
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
    
    def build_request_url(self, url: str, user_agent: str) -> str:
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
    
    def get_danmu_ws_url(self, live_room_real_id: str) -> str:
        """获取弹幕WebSocket URL - 使用原项目的完整实现"""
        user_unique_id = random.randint(7300000000000000000, 7999999999999999999)
        
        # 使用原项目的JS引擎生成签名
        try:
            # 读取JS文件
            js_file_path = os.path.join(os.path.dirname(__file__), 'javascript', 'webmssdk.js')
            with open(js_file_path, 'r', encoding='utf-8') as f:
                js_enc = f.read()
            
            ua = self.get_random_ua()
            
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
            
            # 生成签名参数
            ms_stub = self.get_ms_stub(live_room_real_id, user_unique_id)
            function_caller = f"get_sign('{ms_stub}')"
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
            
            uri = self.build_request_url(
                f"wss://webcast5-ws-web-lf.douyin.com/webcast/im/push/v2/?{'&'.join([f'{k}={v}' for k, v in webcast5_params.items()])}",
                ua)
            
            logger.info(f'使用JS签名生成WebSocket URL成功')
            return uri
            
        except Exception as e:
            logger.warning(f'生成WebSocket URL失败: {e}，使用备用方案')
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
    
    def generate_filename(self, segment_index: int = None) -> str:
        """生成弹幕文件名"""
        if self.video_filename:
            # 使用视频文件名作为基础，替换扩展名为.xml
            base_name = Path(self.video_filename).stem
            # 直接使用视频文件名对应的弹幕文件名，不添加额外的分段索引
            # 因为视频文件名已经包含了正确的分段索引
            return f"{base_name}.xml"
        else:
            # 等待视频文件名设置，暂时返回None
            logger.warning('视频文件名尚未设置，弹幕文件将在视频开始录制后创建')
            return None
    
    def set_video_filename(self, video_filename: str) -> None:
        """设置视频文件名，用于生成对应的弹幕文件名"""
        self.video_filename = video_filename
        logger.info(f'设置视频文件名: {video_filename}')
        
        # 注意：不在这里立即创建文件，而是在第一条弹幕到达时创建
        # 这样可以避免生成空的弹幕文件
    
    def create_danmu_file(self, filename: str) -> None:
        """创建弹幕文件并写入完整的XML模板"""
        filepath = self.output_dir / filename
        with open(filepath, 'w', encoding='UTF-8') as file:
            file.write("<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
                      "<?xml-stylesheet type=\"text/xsl\" href=\"#s\"?>\n"
                      "<i>\n"
                      "</i>\n")  # 立即写入结束标签，确保XML格式完整
        logger.info(f'创建弹幕文件: {filepath}')
    
    def close_danmu_file(self, filename: str) -> None:
        """关闭弹幕文件（XML文件已经完整，无需额外操作）"""
        if filename:
            filepath = self.output_dir / filename
            if filepath.exists():
                logger.info(f'弹幕文件已完成: {filepath}')
    
    def start_new_segment(self) -> None:
        """开始新的分段"""
        # 关闭当前分段文件
        if self.current_segment_file:
            self.close_danmu_file(self.current_segment_file)
        
        # 创建新的分段文件
        filename = self.generate_filename(self.segment_index)
        if filename:
            self.current_segment_file = filename
            self.create_danmu_file(self.current_segment_file)
            self.segment_start_time = time.time()
            self.segment_index += 1
            logger.info(f'开始新的弹幕分段: {self.current_segment_file}')
        else:
            logger.warning(f'无法创建分段文件，视频文件名尚未设置')
            self.current_segment_file = None
    
    def write_danmu(self, user: str, content: str, timestamp: float) -> None:
        """写入弹幕到文件 - 插入到XML结束标签前"""
        try:
            # 如果文件还没创建，先创建文件
            if not self.filename and not self.current_segment_file:
                # 检查是否是分段模式
                if hasattr(self, 'segment_index') and self.segment_index is not None:
                    # 分段模式，创建第一个分段文件
                    filename = self.generate_filename(self.segment_index)
                    if filename:
                        self.current_segment_file = filename
                        self.create_danmu_file(self.current_segment_file)
                        self.segment_start_time = time.time()
                        self.segment_index += 1
                        logger.info(f'创建弹幕分段文件: {self.current_segment_file}')
                    else:
                        logger.warning('无法创建弹幕分段文件，视频文件名尚未设置')
                        return
                else:
                    # 普通模式
                    filename = self.generate_filename()
                    if filename:
                        self.filename = filename
                        self.create_danmu_file(self.filename)
                        logger.info(f'创建弹幕文件: {self.filename}')
                    else:
                        logger.warning('无法创建弹幕文件，视频文件名尚未设置')
                        return
            
            with self.file_lock:
                # 计算弹幕时间戳
                if self.current_segment_file and self.segment_start_time:
                    # 分段模式：使用分段开始时间作为基准，每个分段从0开始
                    second = timestamp - self.segment_start_time
                else:
                    # 普通模式：使用录制开始时间作为基准
                    second = timestamp - self.start_time_t
                
                if self.current_segment_file:
                    # 分段模式
                    filepath = self.output_dir / self.current_segment_file
                elif self.filename:
                    # 普通模式
                    filepath = self.output_dir / self.filename
                else:
                    logger.warning('无弹幕文件可写入')
                    return
                
                # 读取现有内容
                if filepath.exists():
                    with open(filepath, 'r', encoding='UTF-8') as file:
                        content_lines = file.readlines()
                    
                    # 找到 </i> 标签的位置，在其前面插入弹幕
                    danmu_line = f"  <d p=\"{round(second, 2)},1,25,16777215,{int(timestamp * 1000)},0,1602022773,0\" user=\"{user}\">{content}</d>\n"
                    
                    # 在最后一行（</i>）前插入弹幕
                    if content_lines and content_lines[-1].strip() == '</i>':
                        content_lines.insert(-1, danmu_line)
                    else:
                        # 如果没有找到结束标签，追加弹幕和结束标签
                        content_lines.append(danmu_line)
                        content_lines.append('</i>\n')
                    
                    # 写回文件
                    with open(filepath, 'w', encoding='UTF-8') as file:
                        file.writelines(content_lines)
                
                self.danmu_amount += 1
                self.last_danmu_time = timestamp
                
        except Exception as e:
            logger.error(f'写入弹幕失败: {e}')
    
    async def start(self, enable_segment: bool = False, segment_time: int = 1800) -> None:
        """开始录制弹幕"""
        logger.info(f'开始录制 {self.room_name}({self.room_id}) 的弹幕')
        
        # 获取直播间信息
        logger.info('正在获取直播间信息...')
        room_json = await self.get_live_state_json()
        if room_json is None:
            logger.error('无法获取直播间信息，请检查房间ID是否正确')
            return
            
        self.room_real_id = room_json.get('id_str', str(self.room_id))
        logger.info(f'真实房间ID: {self.room_real_id}')
        
        # 检查是否在直播
        status = room_json.get('status', 0)
        if status != 2:
            logger.warning(f'主播未在直播 (状态: {status})')
            return
            
        logger.info('主播正在直播，开始连接弹幕服务器...')
        
        if self.start_time is None:
            self.start_time = time.localtime()
        self.start_time_t = int(time.mktime(self.start_time))
        
        # 初始化分段模式（但不立即创建文件）
        if enable_segment:
            self.segment_index = 0
            self.segment_start_time = None
            logger.info('弹幕分段模式已启用，将在第一条弹幕到达时创建文件')
        else:
            logger.info('弹幕录制已启动，将在第一条弹幕到达时创建文件')
        
        # 创建WebSocket连接
        try:
            ws_url = self.get_danmu_ws_url(self.room_real_id)
            logger.info('连接弹幕服务器...')
            
            self.ws = websocket.WebSocketApp(
                url=ws_url,
                header=self.get_request_headers(),
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
                on_open=self._on_open,
            )
            
            # 在单独线程中运行WebSocket
            def run_websocket():
                self.ws.run_forever()
            
            ws_thread = threading.Thread(target=run_websocket, daemon=True)
            ws_thread.start()
            
            # 如果启用分段，启动分段检查线程
            if enable_segment:
                def segment_checker():
                    while not self.stop_signal:
                        time.sleep(1)
                        if (self.segment_start_time and
                            time.time() - self.segment_start_time >= segment_time):
                            self.start_new_segment()
                
                segment_thread = threading.Thread(target=segment_checker, daemon=True)
                segment_thread.start()
            
            return ws_thread
            
        except Exception as e:
            logger.error(f'连接失败: {e}')
            return None
    
    def stop(self) -> None:
        """停止录制"""
        logger.info('停止弹幕录制...')
        self.stop_signal = True
        if self.ws:
            self.ws.close()
        
        # 注意：文件关闭将在 _on_close 方法中处理，避免重复关闭
    
    def _on_open(self, ws) -> None:
        """WebSocket连接打开"""
        logger.info('弹幕连接已建立，开始接收弹幕...')
        _thread.start_new_thread(self._heartbeat, (ws,))
    
    def _on_message(self, ws, message) -> None:
        """处理弹幕消息"""
        try:
            now = time.time()
            
            # 解析弹幕消息
            parsed_data = parse_danmu_message(message)
            
            if parsed_data:
                # 如果是弹幕消息
                if 'user' in parsed_data and 'content' in parsed_data:
                    user = parsed_data['user']['nickName']
                    content = parsed_data['content']
                    
                    # 写入弹幕到文件
                    self.write_danmu(user, content, now)
                    self.last_danmu_time = now
                    
                    logger.debug(f'[{time.strftime("%H:%M:%S")}] {user}: {content}')
                
                # 如果需要发送ACK
                elif 'needAck' in parsed_data and parsed_data['needAck']:
                    try:
                        ack_data = create_ack_frame(
                            parsed_data.get('logid', 0),
                            parsed_data.get('internalExt', '')
                        )
                        ws.send(ack_data, websocket.ABNF.OPCODE_BINARY)
                    except Exception as e:
                        logger.debug(f'发送ACK失败: {e}')
            
            # 更新最后接收消息时间
            if len(message) > 0:
                self.last_danmu_time = now
                
        except Exception as e:
            logger.error(f'处理弹幕消息失败: {e}')
    
    def _on_error(self, ws, error) -> None:
        """WebSocket错误处理"""
        logger.error(f'弹幕连接错误: {error}')
    
    def _on_close(self, ws, close_status_code, close_msg) -> None:
        """WebSocket连接关闭"""
        logger.info('弹幕连接已关闭')
        
        # 如果是正常停止，关闭文件
        if self.stop_signal:
            if self.current_segment_file:
                self.close_danmu_file(self.current_segment_file)
            elif self.filename:
                self.close_danmu_file(self.filename)
            logger.info(f'弹幕录制结束，共录制 {self.danmu_amount} 条弹幕')
        
        # 重连逻辑
        elif self.retry < self.max_retry:
            self.retry += 1
            logger.info(f'尝试重连弹幕服务器 ({self.retry}/{self.max_retry})...')
            time.sleep(2)
            # 这里可以添加重连逻辑
    
    def _heartbeat(self, ws) -> None:
        """心跳包"""
        t = 9
        while not self.stop_signal and ws.keep_running:
            if t % 10 == 0:
                # 发送心跳包
                try:
                    heartbeat_data = create_heartbeat_frame()
                    ws.send(heartbeat_data, websocket.ABNF.OPCODE_BINARY)
                except Exception as e:
                    logger.debug(f'发送心跳包失败: {e}')
                    # 心跳包发送失败不中断连接
                    
                # 检查是否长时间没有弹幕
                now = time.time()
                if t > 30 and now - self.last_danmu_time > 60:
                    logger.warning('长时间无弹幕，检查主播是否下播...')
                    
            t += 1
            time.sleep(1)


def create_douyin_danmu_recorder(room_id: str, room_name: str, output_dir: str,
                                video_filename: str = None, cookies: str = None) -> DouyinDanmuRecorder:
    """
    创建抖音弹幕录制器的工厂函数
    
    Args:
        room_id: 房间ID
        room_name: 主播名称
        output_dir: 输出目录
        video_filename: 视频文件名（用于保持一致性）
        cookies: Cookie字符串
        
    Returns:
        DouyinDanmuRecorder: 弹幕录制器实例
    """
    return DouyinDanmuRecorder(room_id, room_name, output_dir, video_filename, cookies)