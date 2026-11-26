#!/usr/bin/env bash

# 进入脚本所在目录
cd "$(dirname "$0")" || exit 1

echo "正在扫描可用的 FPGA 固件..."

# 查找所有包含同名 .bit 文件的目录
options=()
shopt -s nullglob
for dir in */; do
    dir=${dir%/} # 去除结尾的斜杠
    if [[ -f "$dir/$dir.bit" ]]; then
        options+=("$dir")
    fi
done

if [[ ${#options[@]} -eq 0 ]]; then
    echo "未找到任何可用的 FPGA 烧录文件 (例如 0514/0514.bit)！"
    echo "请先确保当前目录下存在对应的固件文件夹。"
    exit 1
fi

echo "====================================="
echo "  请选择要下载到 FPGA 的固件版本："
echo "====================================="

PS3="请输入对应的序号并回车: "
select selection in "${options[@]}" "退出"; do
    if [[ "$selection" == "退出" ]]; then
        echo "已取消下载。"
        exit 0
    elif [[ -n "$selection" ]]; then
        target_bit="${selection}/${selection}.bit"
        echo ""
        echo "你选择了: $selection"
        echo "目标文件: $target_bit"
        echo "准备烧录 FPGA..."
        
        # 检查下载器是否存在并且有执行权限
        if [[ ! -x "./xc3sprog" ]]; then
            echo "检测到 xc3sprog 没有执行权限，正在添加权限..."
            chmod +x ./xc3sprog
        fi

        # 执行下载命令
        sudo ./xc3sprog -c sysfsgpio_rpi -v -p 0 "$target_bit"
        
        if [[ $? -eq 0 ]]; then
            echo "====================================="
            echo "         FPGA 烧录完成！"
            echo "====================================="
        else
            echo "====================================="
            echo "         FPGA 烧录失败！"
            echo "请检查连线或权限问题。"
            echo "====================================="
        fi
        break
    else
        echo "无效的选择，请重新输入序号。"
    fi
done
