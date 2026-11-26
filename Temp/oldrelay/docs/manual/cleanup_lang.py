import os
import re

MANUAL_DIR = "/home/pi/Desktop/core/new/docs/manual"

replacements = {
    "为了躲开": "为避免",
    "一般地，": "通常，",
    "一般地": "通常",
    "一般取": "通常取",
    "一般为": "通常为",
    "一般需要": "通常需要",
    "测试仪方予以认可": "系统方予以确认生效",
    "程序提供了": "系统提供了",
    "程序根据": "系统根据",
    "程序自动": "系统自动",
    "我们建议": "建议",
    "我们可以": "可",
    "我们的": "系统的",
    "我们": "系统"
}

def process_language(content):
    for old, new in replacements.items():
        content = content.replace(old, new)
    return content

def main():
    for filename in os.listdir(MANUAL_DIR):
        if not filename.endswith(".md"): continue
        filepath = os.path.join(MANUAL_DIR, filename)
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        new_content = process_language(content)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
    print("Language tone adjustment finished.")

if __name__ == "__main__":
    main()
