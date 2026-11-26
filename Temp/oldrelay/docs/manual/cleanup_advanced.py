import os
import re

MANUAL_DIR = "/home/pi/Desktop/core/new/docs/manual"

def process_mechanical(content):
    # 1. 角度
    content = re.sub(r'\$\s*([0-9\.]+)\s*\^\s*\{\s*\\circ\s*\}\s*\$', r'\1°', content)
    
    # 2. 数字和单位之间的不规则公式如 $1 0 { \sim } 2 0 \mathrm { m s }$
    def fix_range(match):
        text = match.group(0)
        # Remove LaTeX formatting
        text = re.sub(r'\\mathrm\s*\{\s*([a-zA-Z]+)\s*\}', r'\1', text)
        text = text.replace('$', '').replace('{', '').replace('}', '').replace('\\sim', '~').replace('\\ast', '*')
        # Remove spaces between digits
        text = re.sub(r'(?<=\d)\s+(?=\d)', '', text)
        return text.strip()
    
    content = re.sub(r'\$[^$]*?\\sim[^$]*?\$', fix_range, content)
    
    # 3. 带圈数字
    content = re.sub(r'\$\\textcircled\{\s*([0-9]+)\s*\}\$', r'(\1)', content)
    
    # 4. 其他奇怪符号
    content = content.replace('$\\twoheadrightarrow$', '→')
    content = content.replace('$\\nrightarrow$', '↛')
    content = content.replace('$\\bullet$', '-')
    content = content.replace('$\\diamondsuit$', '-')
    content = content.replace('$\\spadesuit$', '-')
    
    # 5. 去除数字内部的多余空格 (e.g. 5 0 . 0 -> 50.0)
    def fix_spaced_numbers(match):
        return match.group(0).replace(' ', '')
    content = re.sub(r'\b\d+(?:\s+\d+)+\b', fix_spaced_numbers, content)
    
    # 6. 空的 $$
    content = content.replace('$$', '')

    # 7. \mathrm, \mathbf etc in formulas
    # We'll just remove the wrapper but keep the inner text
    for wrapper in ['mathrm', 'mathbf', 'boldsymbol', 'pmb', 'mathsf', 'texttt']:
        content = re.sub(r'\\'+wrapper+r'\s*\{\s*([^}]+)\s*\}', r'\1', content)
    
    return content

def main():
    for filename in os.listdir(MANUAL_DIR):
        if not filename.endswith(".md"): continue
        filepath = os.path.join(MANUAL_DIR, filename)
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        new_content = process_mechanical(content)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
    print("Mechanical cleanup finished.")

if __name__ == "__main__":
    main()
