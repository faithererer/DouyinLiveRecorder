# -*- encoding: utf-8 -*-

"""
抖音直播弹幕protobuf解析模块
基于用户成功的弹幕录制脚本实现
"""

import gzip
import json
from typing import Optional, Dict, Any
from google.protobuf import json_format

# 导入真正的protobuf模块
from .dy_pb2 import PushFrame, Response, ChatMessage


def parse_danmu_message(message: bytes) -> Optional[Dict[str, Any]]:
    """
    解析弹幕消息 - 使用真正的protobuf解析
    
    Args:
        message: WebSocket接收到的二进制消息
        
    Returns:
        解析后的弹幕数据，包含用户名和内容
    """
    try:
        # 解析推送帧
        wssPackage = PushFrame()
        wssPackage.ParseFromString(message)
        logid = wssPackage.logid
        
        # 解压缩payload
        decompressed = gzip.decompress(wssPackage.payload)
        payloadPackage = Response()
        payloadPackage.ParseFromString(decompressed)
        
        # 处理消息
        for msg in payloadPackage.messagesList:
            if msg.method == 'WebcastChatMessage':
                chatMessage = ChatMessage()
                chatMessage.ParseFromString(msg.payload)
                data = json_format.MessageToDict(chatMessage, preserving_proto_field_name=True)
                
                # 返回弹幕数据
                return {
                    'user': data.get('user', {}),
                    'content': data.get('content', ''),
                    'logid': logid,
                    'needAck': payloadPackage.needAck,
                    'internalExt': payloadPackage.internalExt
                }
        
        # 如果需要ACK但没有弹幕消息
        if payloadPackage.needAck:
            return {
                'needAck': True,
                'logid': logid,
                'internalExt': payloadPackage.internalExt
            }
            
        return None
        
    except Exception as e:
        # 解析失败时返回None
        return None


def create_ack_frame(logid: int, internal_ext: str) -> bytes:
    """
    创建ACK确认帧
    
    Args:
        logid: 日志ID
        internal_ext: 内部扩展信息
        
    Returns:
        序列化的ACK帧
    """
    try:
        obj = PushFrame()
        obj.payloadType = 'ack'
        obj.logid = logid
        obj.payloadType = internal_ext
        return obj.SerializeToString()
    except:
        return b'ack'


def create_heartbeat_frame() -> bytes:
    """
    创建心跳帧
    
    Returns:
        序列化的心跳帧
    """
    try:
        obj = PushFrame()
        obj.payloadType = 'hb'
        return obj.SerializeToString()
    except:
        return b'hb'