import os
import re
from collections import defaultdict

MANUAL_DIR = "/home/pi/Desktop/core/new/docs/manual"

def scan_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    issues = defaultdict(list)
    
    # 1. Math formulas
    formulas = re.findall(r'\$.*?\$', content)
    if formulas:
        issues['formulas'] = list(set(formulas))[:10] # Top 10 unique formulas
        
    # 2. Subjective language / informal
    phrases = ["我们", "为了躲开", "一般", "程序", "测试仪方予以认可", "可以", "自动", "非常"]
    for phrase in phrases:
        count = content.count(phrase)
        if count > 0:
            issues['language'].append(f"{phrase}: {count}次")
            
    # 3. Weird characters (OCR leftovers)
    weird_chars = re.findall(r'[■◆●★☆〇《》【】『』「」℃℉‰※\u25A0-\u25FF\u2600-\u26FF]', content)
    if weird_chars:
        issues['weird_chars'] = list(set(weird_chars))
        
    # 4. Heading issues (e.g., # 1、)
    heading_issues = re.findall(r'^#+ \d+[、，。].*', content, flags=re.MULTILINE)
    if heading_issues:
        issues['headings'] = heading_issues[:5]
        
    # 5. Spaces inside words or numbers like 1 0
    spaced_numbers = re.findall(r'\b\d\s+\d\b', content)
    if spaced_numbers:
        issues['spaced_numbers'] = spaced_numbers[:5]
        
    return issues

print("--- 检查报告 ---")
for i in range(1, 17):
    prefix = f"{i:02d}_"
    file = next((f for f in os.listdir(MANUAL_DIR) if f.startswith(prefix)), None)
    if file:
        filepath = os.path.join(MANUAL_DIR, file)
        issues = scan_file(filepath)
        if issues:
            print(f"\n[{file}]")
            for k, v in issues.items():
                print(f"  - {k}: {v}")
