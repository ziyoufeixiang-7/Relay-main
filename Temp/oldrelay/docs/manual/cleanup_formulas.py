import os

MANUAL_DIR = "/home/pi/Desktop/core/new/docs/manual"

replacements = {
    # 10_线路保护定值校验.md
    r"m { = } 1 . 1": r"m = 1.1",
    r"U f f = 2 * I * { D Z s e t } + ( 1 - 1 . 0 5 * m ) * \sqrt { 3 } * U e": r"$$ U_{ff} = 2 \times I \times Z_{set} + (1 - 1.05m) \times \sqrt{3} U_e $$",
    r"U f f = 2 * I * { D Z s e t } + ( 1 - 1 . 3 * m ) * \sqrt { 3 } * U e": r"$$ U_{ff} = 2 \times I \times Z_{set} + (1 - 1.3m) \times \sqrt{3} U_e $$",
    r"U f = ( 1 + K ) * I * D Z s e t + ( 1 - 1 . 0 5 * m ) * U e": r"$$ U_f = (1+K) \times I \times Z_{set} + (1 - 1.05m) \times U_e $$",
    
    # 11_阻抗特性测试.md
    r"( 3 \ast \mathbf { Z 1 } )": r"(3 \times Z_1)",
    r"\texttt { R + j } \texttt { X }": r"R + jX",
    r"1 6 5 ^ { \circ }": r"165^\circ",

    # 12_差动试验.md
    r"\Upsilon / \triangle / \triangle \ – 1": r"Y/△/△-1",
    r"{ \vert \boldsymbol { r } = \vert } 2 ^ { \prime \prime }": r"I_r = I_2",
    r"1 \times 6 \%": r"I \times 6\%",
    r"I d = I 1": r"I_d = I_1",

    # Formatting headers properly
    "\n# ": "\n### ",
    "\n## ": "\n### "
}

def process_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    for old, new in replacements.items():
        content = content.replace(old, new)
        
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

for filename in os.listdir(MANUAL_DIR):
    if filename.endswith(".md"):
        process_file(os.path.join(MANUAL_DIR, filename))

print("Formula cleanup complete.")
