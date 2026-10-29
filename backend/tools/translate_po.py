"""
Django .po 批量翻译脚本：调用百度翻译 API 填写空 msgstr。

开发/本地化辅助工具，非线上请求路径。需配置 APPID / SECRET_KEY。
入口：translate_po_file(po_path, to_lang)；baidu_translate 单条翻译。
"""
import hashlib
import os
import random

import polib
import requests

# 百度翻译 API（从环境变量读取，勿提交真实密钥）
APPID = os.getenv("BAIDU_TRANSLATE_APPID", "")
SECRET_KEY = os.getenv("BAIDU_TRANSLATE_SECRET", "")


def baidu_translate(q, from_lang='auto', to_lang='auto'):
    """调用百度翻译 API；失败返回原文。from_lang/to_lang 见百度语言代码。"""
    print(f"开始翻译：{q}")
    print(f"目标语言：{to_lang}")
    url = 'https://fanyi-api.baidu.com/api/trans/vip/translate'
    salt = random.randint(32768, 65536)
    sign_str = f"{APPID}{q}{salt}{SECRET_KEY}"
    sign = hashlib.md5(sign_str.encode()).hexdigest()
    params = {
        'q': q,
        'from': from_lang,  # 自动识别源语言（关键修复）
        'to': to_lang,
        'appid': APPID,
        'salt': salt,
        'sign': sign
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        result = response.json()

        # 调试信息（可选保留）
        # print(f"原文：{q} | 语言：{from_lang}→{to_lang} | API返回：{result}")

        if 'trans_result' in result and len(result['trans_result']) > 0:
            return result['trans_result'][0]['dst']
        elif 'error_code' in result:
            print(f"翻译失败[{q}]：{result['error_code']} - {result.get('error_msg', '未知错误')}")
    except Exception as e:
        print(f"请求异常[{q}]：{str(e)}")
    return q  # 失败时返回原文


def translate_po_file(po_path, to_lang):
    """批量翻译 .po：仅处理 msgstr 为空且 msgid 非空的条目，原地保存。"""
    print(f"开始处理：{po_path}")
    print(f"目标语言：{to_lang}")
    try:
        po = polib.pofile(po_path)
        translated_count = 0

        # 遍历所有未翻译的非空条目
        for entry in po:
            if not entry.msgstr and entry.msgid.strip():
                # 源语言设为auto，自动识别中英（关键修复）
                translated_text = baidu_translate(entry.msgid, from_lang='auto', to_lang=to_lang)
                if translated_text != entry.msgid:
                    entry.msgstr = translated_text
                    translated_count += 1
                    print(f"已翻译：{entry.msgid} → {translated_text}")

        po.save()
        print(f"\n翻译完成！共处理 {len(po)} 条，成功翻译 {translated_count} 条")
        print(f"结果已保存到：{po_path}")

    except Exception as e:
        print(f"处理PO文件失败：{str(e)}")


if __name__ == "__main__":
    # 翻译日语（百度日语代码：jp）
    # translate_po_file(
    #     po_path=r"E:\project\1\django-template\locale\ja\LC_MESSAGES\django.po",
    #     to_lang="jp"
    # )

    # 如需翻译其他语言，取消注释并修改（百度语言代码参考）
    # 韩语：kor，德语：de，法语：fra，英语：en
    translate_po_file(
        po_path=r"E:\project\1\django-template\locale\ko\LC_MESSAGES\django.po",
        to_lang="kor"
    )
