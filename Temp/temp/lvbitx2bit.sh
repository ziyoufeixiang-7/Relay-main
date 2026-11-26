#!/usr/bin/env bash

# 进入当前脚本所在目录
cd "$(dirname "$0")" || exit 1

shopt -s nullglob
lvbitx_files=(*.lvbitx)

if [[ ${#lvbitx_files[@]} -eq 0 ]]; then
    echo "No .lvbitx files found in the current directory."
    exit 0
fi

for filepath in "${lvbitx_files[@]}"; do
    echo "Processing $filepath..."
    
    # 用 awk 健壮地提取多行或单行内容
    expected_md5=$(awk '/<BitstreamMD5>/{flag=1; gsub(/.*<BitstreamMD5>/,""); if(/<\/BitstreamMD5>/){gsub(/<\/BitstreamMD5>.*/,""); print; flag=0; next}} flag{if(/<\/BitstreamMD5>/){gsub(/<\/BitstreamMD5>.*/,""); print; flag=0} else {print}}' "$filepath" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')
    
    b64_data=$(awk '/<Bitstream>/{flag=1; gsub(/.*<Bitstream>/,""); if(/<\/Bitstream>/){gsub(/<\/Bitstream>.*/,""); print; flag=0; next}} flag{if(/<\/Bitstream>/){gsub(/<\/Bitstream>.*/,""); print; flag=0} else {print}}' "$filepath" | tr -d '[:space:]')
    
    if [[ -z "$expected_md5" || -z "$b64_data" ]]; then
        echo "  Skipping: Bitstream or BitstreamMD5 not found."
        continue
    fi
    
    temp_bit=$(mktemp)
    # 解码 Base64
    echo "$b64_data" | base64 -d > "$temp_bit" 2>/dev/null
    if [[ $? -ne 0 ]]; then
        echo "  Error decoding base64."
        rm -f "$temp_bit"
        continue
    fi
    
    # 计算实际 MD5
    actual_md5=$(md5sum "$temp_bit" | awk '{print $1}' | tr '[:upper:]' '[:lower:]')
    
    if [[ "$actual_md5" != "$expected_md5" ]]; then
        echo "  MD5 mismatch!"
        echo "    Expected: $expected_md5"
        echo "    Actual:   $actual_md5"
        rm -f "$temp_bit"
        continue
    else
        echo "  MD5 check passed ($actual_md5)."
    fi
    
    # 创建目录并移动文件
    basename="${filepath}"
    name_without_ext="${basename%.*}"
    dir_path="${name_without_ext}"
    
    mkdir -p "$dir_path"
    
    bit_filepath="${dir_path}/${name_without_ext}.bit"
    mv "$temp_bit" "$bit_filepath"
    echo "  Saved bitstream to $bit_filepath"
    
    mv "$filepath" "${dir_path}/$basename"
    echo "  Moved $basename to ${dir_path}/"
    echo "  Successfully processed $basename."
    echo
done
