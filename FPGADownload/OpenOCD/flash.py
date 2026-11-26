#!/usr/bin/env python3
"""
flash.py - Flash XC6SLX25 SPI (M25P16) via OpenOCD on Raspberry Pi 5.
Usage: sudo python3 flash.py <fpga.bit | --detect>
"""
import base64, os, struct, subprocess, sys, tempfile, zlib

OPENOCD_CFG = """
adapter driver linuxgpiod
adapter gpio tck 25 -chip 0
adapter gpio tms 24 -chip 0
adapter gpio tdi 23 -chip 0
adapter gpio tdo 22 -chip 0
adapter speed 100
transport select jtag
jtag newtap xc6s tap -irlen 6 -ignore-version -expected-id 0x04004093
pld create xc6s.pld virtex2 -chain-position xc6s.tap
target create xc6s.proxy testee -chain-position xc6s.tap
flash bank spi0 jtagspi 0x0 0 0 0 xc6s.proxy 0x02
"""

_BSCAN_B64 = (
    "eNrt3WuM5fmZH/TzXOp+v1edququbhLAbJKdsZ31smtn2+QiRdpFESISShCL15slwGqBeAUOvEj7hsYSKDNEWmShBFtcBIYXIEUC"
    "kmjVXnMTCpgor/IiQs4GARJaGYEUgaIdTp3pnvH5lD1te7qnyvHv6e0tf7truqt+3/P/d53qrs8zWdv51lvfJpP4xOQDv/CpT37i"
    "V37+U//iP/vzn/7kT3zqlz/9wd/7e37lk7/403/0U3/iT/3hP/CxVz79h57O5BcmW2/9/Cc/9c986IMfnnxysvHBV179yI+/+sqP"
    "v/ITk1+crL36kZ/68Ad/6tVXJn9iUn9r8ibz1S/90V965SuTlavJq1/p1179+Mcmr35i7bde/Y2ePPpzr3xz8o1Xvjb5zNXked8+"
    "9JmP/vevfmb1Mx9+MJl8MCb3X/3mm29+6MFk6UMfn/SHIiYf/MTsxz8z+/6V2fevzb5/czL50FfO/9aHfmMym+tf4ZWH1/9r9pbE"
    "H/mnZ//j45Pbnw/+mb82e3vyxvv6i89eYf42BzkXckwm5CD7+kVu8hJ5mbxCXiWvkdfJG+RN8hZ5m7xD3iXvkffJB+RD8hH5mHxK"
    "PiNPyefkC/LlQk76S/pL+kv6S/pL+kv6S/pK+kr6SvpK+kr6SfpI+kj6SPpI+kj6yMkJ+ZR8Rl487+L6Kc6/OP/i/IvzL86/OP/i"
    "/Ivrpzj/4vyL8y/Ov7heij6K66Xop+in6Kfop+in6Kfop+in6Ke4forrp7h+6kaf9xZy02/Tb9Nv02/Tb9Nv02/Tb9Nv02/Tb9Nv"
    "02/Tb9Nv02/Tb9Nv02/Tb9Nv02/Tb9PvEue/xPkvcf5LnP8S57/E+S9x/kuc/xLnv8T9bok+luhjiT6W6GOJPpboY4k+luhjiT6W"
    "6GOJPpboY4k+luhj6UYfZ+Qp+Zx8QV683pbpd5l+l+l3mX6X6XOZPpfpc5k+l+lzmT6X6XOZPpfpc5k+l+lzmT6X6XOZPpfpc5k+"
    "l+lzmT6X6XOZPpfpc5k+l+lzmT6Xb/S5eP9cod8V+l2h3xX6XeH6XaHvFfpeoe8V+l6h7xX6XqHvFfpeoe8V+l6h7xX6XqHvFfpe"
    "oe8V+l6h7xX6XqHvVc5/lfNf5fxXOf9Vzn+V81/l/Fc5/1XOf5XzX+X8Vzn/Vc5/lfNf5fxXOf9Vzn+V81/l/Fc5/1XOf5XzX+X8"
    "V2+c/yn5jDwln5MvyIvX2xr9rtHvGv2u0e8a/a7R7xr9rtHvGv2u0e8a/a7R7xr9rtHvGv2u0e8a/a7R7xr9rtHvGv2u0e8a/a7R"
    "7xr9rtHvGv2u0e8a/a7d6HfxfrpO3+v0u06/6/S7Tr/r9LtOv+v0u06/6/S7Tr/r9LtOv+v0u06/6/S7Tr/r9LtOv+v0u06/6/S7"
    "Tr/r9LtOv+v0u06/6/S7Tr8b9LnB9btBvxv0u0G/G/S7Qb8b9LtBvxv0u0G/G/S7Qb8b9LtBvxv0u0G/G/S7Qb8b9LtBvxv0u0G/"
    "G/S7Qb8b9LtBvxv0u0G/Gzf6Xbx+N+l7k7436XuTvjfpe5O+N+l7k7436XuTvjfpe5O+N+l7k7436XuTvjfpe5O+N+l7k7436XuT"
    "vjfpe5O+N+l7k7436XuTvjfpe5O+t+h3i3636HeLfrfod4t+t+h3i3636HeLfrfod4t+t+h3i3636HeLfrfod4t+t+h3i3636HeL"
    "frfod4t+t+h3mz62Of9tzn+b89/m/Lc5/23Of5vz3+b8tzn/bc5/m/Pf5vy3Of9tzn+b89/m/Lc5/23Of5vz3+b8tzn/bc5/+8b5"
    "n5Gn5HPyBfmSvHg/3aHfHa63Hfreoe8d+t6h7x363qHvHfreoe8d+t6h7x363qHvHfreoe8d+t6h7x363qHvHfreoe8d+t6h7x36"
    "3qHvHfreoe8d+t6h71363aXfXfrdpd9d+t2l31363aXfXfrdpd9d+t2l31363aXfXfrdpd9d+t2l31363aXfXfrdpd9d+t2l3136"
    "3aXfXfrdpd9d+t290e/i9bxH33v0vUffe/S9R9979L1H33v0vUffe/S9R9979L1H33v0vUffe/S9R9979L1H33v0vUffe/S9R997"
    "9L1H33v0vUffe/S9R9979L1Pv/v0uU+f+/S5T5/79LlPn/v0uU+f+/S5T5/79LlPn/v0uU+f+/S5T5/79LlPn/v0uU+f+/S5T5/7"
    "9LlPn/v0uU+f+/S5f6PPxev3gH4PuH4P6PuAvg/o+4C+D+j7gL4P6PuAvg/o+4C+D+j7gL4P6PuAvg/o+4C+D+j7gL4P6PuAvg/o"
    "+4C+D+j7gL4P6PuAvg/o+4C+D+j7kL4P6fuQvg/p+5C+D+n7kL4P6fuQvg/p+5C+D+n7kL4P6fuQvg/p+5C+D+n7kL4P6fuQvg/p"
    "+5C+D+n7kL4P6fuQvg/p+5C+D+n7kL6P6PuIvo/o+4i+j+j7iL6P6PuIvo/o+4i+j+j7iL6P6PuIvo/o+4i+j+j7iL6P6PuIvo/o"
    "+4i+j+j7iL6P6PuIvo/o+4i+j+j7iL6P6feYPo/p85g+j+nzmD6P6fOYPo/p85g+j+nzmD6P6fOYPo/p85g+j+nzmD6P6fOYPo/p"
    "85g+j+nzmD6P6fOYPo/p85g+j2/0uXj9ntDvCdfvCX2f0PcJfZ/Q9wl9n9D3CX2f0PcJfZ/Q9wl9n9D3CX2f0PcJfZ/Q9wl9n9D3"
    "CX2f0PcJfZ/Q9wl9n9D3CX2f0PcJfZ/Q9yn9ntLvKf2e0u8p/Z7S7yn9ntLvKf2e0u8p/Z7S7yn9ntLvKf2e0u8p/Z7S7yn9ntLv"
    "Kf2e0u8p/Z7S7yn9ntLvKf2e0u8p/Z7e6Hfxej6j7zP6PqPvM/o+o+8z+j6j7zP6PqPvM/o+o+8z+j6j7zP6PqPvM/o+o+8z+j6j"
    "7zP6PqPvM/o+o+8z+j6j7zP6PqPvM/o+o+8z+p7S75Q+p/Q5pc8pfU7pc0qfU/qc0ueUPqf0OaXPKX1O6XNKn1P6nNLnlD6n9Dml"
    "zyl9TulzSp9T+pzS55Q+p/Q5pc/pjT4Xr99z+j3n+j2n73P6Pqfvc/o+p+9z+j6n73P6Pqfvc/o+p+9z+j6n73P6Pqfvc/o+p+9z"
    "+j6n73P6Pqfvc/o+p+9z+j6n73P6Pqfvc/q+oN8L+r2g3wv6vaDfC/q9oN8L+r2g3wv6vaDfC/q9oN8L+r2g3wv6vaDfC/q9oN8L"
    "+r2g3wv6vaDfC/q95PwvOf9Lzv+S87/k/C85/0vO/5Lzv+T8Lzn/S87/kvO/5PwvOf9Lzv+S87/k/C85/0vO/5Lzv+T8Lzn/S87/"
    "8sb5n5LPyFPyOfmCfElevJ/eo+979HuPfu/R7z36vUe/9+j3Hv3eo9979HuPfu/R7z36vUe/9+j3Hv3eo9979HuPfu/R7z36vUe/"
    "9+j3Hv3eo9979HuPfu/R7z36vU+f97l+79Pvffq9T7/36fc+/d6n3/v0e59+79Pvffq9T7/36fc+/d6n3/v0e59+79Pvffq9T7/3"
    "6fc+/d6n3/v0e8X5X3GeV5zfFed1xXldcR5XvP9XvH9XvH9XvD9XN97eM/Li/eIB788DHk8PeDw94PH0gMfPA97/BzweHtDnA/p8"
    "wPv7gPf3AX0+4P1/QJ8PuJ4e8v4+5P19yPv3kPfvIe/fQ66Ph/T9kL4fch4PuT4e8nh4yPXxkMfHQ87zIef5kPN8yHk+5Dwfcp4P"
    "uR4e8vh6yOPrIfe7h9zvHnK/e0g/k0m9/TXGn33247NH7S3Nm7c+k/qNX51/nesJZ3JL8+pdOJOzv/5/zL8WdfLuZ/K8h83zH1ZX"
    "PzyPk5/61/7z+Zms3vbjZPXunMlP/qOP5meyfTeuncnaHTiTe7/31fmZ7N6NM3ntLjxO9v/Y+vxMptdnEn27J7I7uf15nLf4m2fe"
    "lfvH9C/8kfnXxK/d+rXy+p25p354+5fnX6d+62fy6O78OXO69KfnX/vbt30mfXfO5Oof/6X513vdthf0zp3sd/yX/+NzvaBtnh/u"
    "k4/JU/K9SXyH5y+3dLe4G/fQf/hfnn/e4bt+rP7eP0p/8sP2nO7iL17NzyRv+eON1Vv/QCNu/2OdJ3fncdG/+jPzx8Xyy75/PPnh"
    "uVZWP/dwfiYv8Xntd74MPvtdfv4uPK/9J392fiYb8zP5+u1dO7fJAF7dtVZ+15//r+et3PZnG+oOnUn+f79/fia7t/tIzVv/pR7f"
    "pUfqzp99dd7K4e0+Uj97l85k+pEH8zM5vrVH6h34xM/vWr1j99QP/EcfmLfy1mfm/uCtHMrej/xn5CZ5y+993r2PS7cO/+z875dv"
    "PK99789nf2j/rvIf/Pin52ey9OL/XHneU9Wlu3om+dG/Mz+T1ff/uX7e/l1rcjfmyd17XHzkj31w/rjYvMU/2e7WZ2KufrQep1d3"
    "9TP3G//G1fyReXIbn7n/1mxu/OAHfvl/fe5n7h/wL5MeTM6efSb+Fq6umNypufX7cN+BzzU/enQXrq7t/+ob83/39vbHje/948X3"
    "duO8C8+9/5f/d34mtfBx4/MeMc9/RD3vNXbv8JnsfepvzM9k6f37HM3qXX+c/L5/4n+an8nxrf97jNU7cybHv/2zrzy8/uPmzuzl"
    "+Yf+/P/zvD+tg708wV6eWV74d+fBnp5gT0+wpyfixn/f5CXyMnmFvEpeI6+TN8ib5C3yNnmHvEveI++TD8iH5CPyMfmEfEo+I0/J"
    "5+QL8sK/+w729gR7e4K9PcHenmBvT7C3J9jbM8ur5DXyOnmDvEneIm+Td8i75D3yPvmAfEg+Ih+TT8in5DPylHxOviAv9sleoGAv"
    "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"
    "TV4iL5NXyKvkNfI6eYO8Sd4ib5N3yLvkPfI++YB8SD4iH5NPyKfkM/KUfE6+IC88p04MrsTgSgyuxOBKDK7E4EoMrsTgSgyuxOBK"
    "DK7E4EoMrsTgSgyuxOBKDK7E4EoMrsTgSgyuxOBKDK7E4EoMrsTgSgyuxOBKDK7E4EoMrsTgSgyuxOBKDK7E4EoMrsTgSgyuxOBK"
    "DK7E4EoMrsTgSgyuxOBKDK7E4EoMrsTgSgyuxOBKDK7E4EoMrsTgSgyuxODKutHvwueoE4MrMbcScysxtxJzKzG3EnMrMbcScysx"
    "txJzKzG3EnMrMbcScysxtxJzKzG3EnMrMbcScysxtxJzKzG3EnMrMbcScysxtxJzKzG3EnMrMbcScysxtxJzKzG3EnMrMbcScysx"
    "txJzKzG3EnMrMbcScysxtxJzKzG3culGH2fkKfmcfEFevN4wtxJzKzG3EnMrMbcScysxtxJzKzG3EnMrMbcScysxtxJzKzG3EnMr"
    "MbcScysxtxJzKzG3EnMrMbcScysxtxJzKzG3EnMrMbdy+Ua/i/dTzK3E3ErMrcTcSsytxNxKzK3E3ErMrcTcSsytxNxKzK3E3ErM"
    "rcTcSsytxNxKzK3E3ErMrcTcSsytxNxKzK3E2EqMrcTYSoytxNhKjK3E2EqMrcTYSoytxNhKjK3E2EqMrcTYSoytxNhKjK3E2EqM"
    "rcTYytUb531KPiNPyefkC/Li9YWxlRhbibGVGFuJsZUYW4mxlRhbibGVGFuJsZUYW4mxlRhbibGVGFuJsZUYW4mxlRhbibGVGFuJ"
    "sZUYW4mxlRhbibGVGFuJsZVrN/pdvH9ibCWmVmJqJaZWYmolplZiaiWmVmJqJaZWYmolplZiaiWmVmJqJaZWYmolplZiaiWmVmJq"
    "JaZWYmolplZiaiWmVmJqJaZWYmolplZiaiWmVmJqJaZWYmolplZiaiWmVmJqJaZWYmolplZiaiWmVmJqJaZWYmolplZiaiWmVmJq"
    "JaZWYmolplZiaiWmVmJqJaZWbtzod/H6xdRKDK3E0EoMrcTQSgytxNBKDK3E0EoMrcTQSgytxNBKDK3E0EoMrcTQSgytxNBKDK3E"
    "0EoMrcTQSgytxNBKDK3E0EoMrcTQSgytxNBKDK3E0EoMrcTQSgytxNBKDK3E0EoMrcTQSgytxNBKDK3E0EoMrcTQSgytxNBKDK3E"
    "0EoMrcTQSgytxNBKzKzEzErMrMTMSsysxMxKzKzEzErMrMTMSsysxMxKzKzEzErMrMTMSsysxMxKzKzEzErMrMTMyu0b539GnpLP"
    "yRfkS/Li/RMzKzGzEjMrMbMSMysxsxIzKzGzEjMrMbMSMysxsxIzKzGzEjMrMbMSMysxsxIzKzGzEjMrMbMSMysxsxIzKzGzEjMr"
    "MbMSMysxsxIzKzGyEiMrMbISIysxshIjKzGyEiMrMbISIysxshIjKzGyEiMrMbISIysxshIjKzGyEiMrMbISIysxshIjKzGyEiMr"
    "MbJy90afi9cvRlZiZCVGVmJkJUZWYmQlRlZiZCVGVmJkJUZWYmQlRlZiZCVGVmJkJUZWYmQlRlZiZCVGVmJkJUZWYmQlRlZiZCVG"
    "VmJkJUZWYmQlRlZiZCVGVmJkJUZWYmQlRlZiZCVGVmJkJUZWYmQlRlZiZCVGVmJkJUZWYmQlRlZiZCVGVmJkJUZWYmQlRlZiZCVG"
    "VmJkJUZW7t/od/F6xshKjKzEyEqMrMTISoysxMhKjKzEyEqMrMTISoysxMhKjKzEyEqMrMTISoysxMhKjKzEyEqMrMTISoysxMhK"
    "jKzEyEqMrMTISoysxMhKjKzEyEqMrMTISoysxMhKjKzEyEqMrMTISoysxMhKjKzEyEqMrMTISoysxMhKjKzEyEqMrMTISoysxMhK"
    "jKzEyEqMrMTISoysxMhKjKzEyEqMrMTISoysxMhKjKzEyEqMrMTISoysxMhKjKzEyEqMrMTISoysxMhKjKzEyEqMrMTISoysxMhK"
    "jKzEyEqMrMTISoysxMhKjKzEyEqMrMTISoysxMhKjKzEyEqMrMTISoysxMhKjKzEyEqMrMTISoysxMhKjKzEyEqMrMTISoysxMhK"
    "jKzEyEqMrMTISoysxMjK4xv9Ll7PGFmJkZUYWYmRlRhZiZGVGFmJkZUYWYmRlRhZiZGVGFmJkZUYWYmRlRhZiZGVGFmJkZUYWYmR"
    "lRhZiZGVGFmJkZUYWYmRlRhZiZGVGFmJkZUYWYmRlRhZiZGVGFmJkZUYWYmRlRhZiZGVGFmJkZUYWYmRlRhZiZGVGFmJkZUYWYmR"
    "lRhZiZGVGFmJkZUYWYmRlRhZeXqj38XrGSMrMbISIysxshIjKzGyEiMrMbISIysxshIjKzGyEiMrMbISIysxshIjKzGyEiMrMbIS"
    "IysxshIjKzGyEiMrMbISIysxshIjKzGyEiMrMbISIysxshIjKzGyEiMrMbISIysxshIjKzGyEiMrMbISIysxshIjKzGyEiMrMbIS"
    "IysxshIjKzGyEiMrMbISIysxshIjK6c3+l28njGyEiNrlpNc5CYvkZfJK+RV8hp5nbxB3iRvkbfJO+Rd8h55n3xAPiQfkY/JJ+RT"
    "8hl5Sj4nX5AX+8bcSsytxNxKzK1ZXiIvk1fIq+Q18jp5g7xJ3iJvk3fIu+Q98j75gHxIPiIfkxf7xORKTK7E5EpMrsTkSkyuxORK"
    "TK7E5EpMrsTkSkyuxORKTK7E5EpMrsTkSkyuxORKTK7E5EpMrsTkyssb539KPiNPyefkC/IlefH+icmVmFyJyZWYXInJlZhcicmV"
    "mFyJyZWYXInJlZhcicmVmFyJyZWYXInJlZhcicmVmFyJyZWYXInJlZhcicmVmFyJyZWYXInJlZhcicmVmFyJyTXLRW7yEnmZvEJe"
    "Ja+R18kb5E3yFnmbvEPeJe+R98kH5EPyEfmYfEJe7BejKzG6EqMrMboSoysxuhKjKzG6EqMrMboSoysxuhKjKzG6EqMrMboSoysx"
    "uhKjKzG6EqMrMboSoyuvbpz/GXlKPidfkC/Ji/fTB1xvmF2J2ZWYXYnZlZhdidmVmF2J2ZWYXYnZlZhdidmVmF2J2ZWYXYnZlZhd"
    "idmVmF2J2ZWYXYnZlZhdidmVmF2J2ZWYXYnZlZhdidmVmF2J2ZWYXYnZlZhdidmVmF2J2ZWYXYnZlZhdidmVmF2J2ZWYXYnZlZhd"
    "idmVmF2J2ZWYXYnZlZhdidmVmF2J2ZUPb3x98fdgdr33efScn796+vIOuAFr/8l0/nWEL/xMdn/A/+4OnMlP//ivzL925EWfyaPJ"
    "D++ZHH74d87/Pf57PJNHL+ww78CZnL/xW/N/4/x9nsmjl3bfuQs2y3/zt+f/DvU5Z/LyzuAOnsnPbH5k/m/7OJOryW3NHTiTs9+/"
    "Ov/809tezdX3+GfmD/4az/n53d+6eq5X85CP76/e/eOJ9/t9ugO9/u7/+V+Zn9Owmb7Nv/vn/uArD6/dwDtjM/3Y7370vMd6YTMV"
    "NlNhMRUWU8WN1y9yk5fIy+QV8ip5jbxO3iBvkrfI2+Qd8i55j7xPPiAfko/Ix+QT8in5jDwln5MvyAvPlSrpF5upsJkKm6mwmQqb"
    "qbCZCpupsJkKm6mwmQqbqbCZCpupsJkKm6mwmQqbqbCZCpupsJkKm6mwmQqbqbCZCpupsJkKm6mwmQqbqbCZCpupsJkKm6mwmQqb"
    "qbCZCpupsJkKm6mwmQqbqbCZCpupsJkKm6mwmQqbqbCZCpupsJkKm6mwmQqbqbCZCpupsJkKm6mwmapu9LvwucnCZprlICe5yE1e"
    "Ii+TV8ir5DXyOnmDvEneIm+Td8i75D3yPvmAfEg+Ih+TT8iLfWM1FVZTYTUVVlNhNRVWU2E1FVZTYTUVVlNhNRVWU2E1FVZTYTUV"
    "VlNhNRVWU2E1FVZTYTUVVlNhNRVWUy3d6OOMPCWfky/Ii9cfVlNhNRVWU2E1FVZTYTUVVlNhNRVWU2E1FVZTYTUVVlNhNRVWU2E1"
    "FVZTYTUVVlNhNRVWU2E1FVZTYTUVVlNhNRVWU2E1FVZTLd/od/H+itVUWE2F1VRYTYXVVFhNhdVUWE2F1VRYTYXVVFhNhdVUWE2F"
    "1VRYTYXVVFhNhdVUWE2F1VRYTYXVVFhNhdU0y0FOcpGbvEReJq+QV8lr5HXyBnmTvEXeJu+Qd8l75H3yAfmQfEQ+Jnv+p+Qz8pR8"
    "Tr4gL15v2E2F3VTYTYXdVNhNhd1U2E2F3VTYTYXdVNhNhd1U2E2F3VTYTYXdVNhNhd1U2E2F3VTYTYXdVNhNhd1U2E2F3VTYTYXd"
    "VNhNtXaj38X7KXbTLAc5yUVu8hJ5mbxCXiWvkdfJG+RN8hZ5m7xD3iXvkffJB+RD8hH5mHxCPiWfkafkc/IFebFvLKfCciosp8Jy"
    "KiynwnIqLKfCciosp8JyKiynwnIqLKfCciosp8JyKiynwnIqLKfCciosp8JyKiynwnIqLKfCciosp8JyKiyn2rjR7+L1jOU0y0FO"
    "cpGbvEReJq+QV8lr5HXyBnmTvEXeJu+Qd8l75H3yAfmQfEQ+Jp+QT8ln5Cn5nHxBXuwb26mwnQrbqbCdCtupsJ0K26mwnQrbqbCd"
    "CtupsJ0K26mwnQrbqbCdCtupsJ0K26mwnQrbqbCdCtupsJ0K26mwnWY5yEkucpOXyMvkFfIqeY28Tt4gb5K3yNvkHfIueY+8Tz4g"
    "H5KPyMfkE7J9nJGn5HPyBfmSvHh/xXoqrKfCeiqsp8J6KqynwnoqrKfCeiqsp8J6KqynwnoqrKfCeiqsp8J6KqynwnoqrKfCeiqs"
    "p8J6KqynwnoqrKfCeiqsp8J6Kqyn2qVPrKfCeiqsp8J6KqynwnoqrKfCeiqsp8J6KqynwnoqrKfCeiqsp8J6KqynwnoqrKfCeiqs"
    "p8J6KqynwnoqrKfCeiqsp9q90efi9Yv1VFhPhfVUWE+F9VRYT4X1VFhPhfVUWE+F9VRYT4X1VFhPhfVUWE+F9VRYT4X1VFhPhfVU"
    "WE+F9VRYT4X1VFhPhfVUWE+F9VRYT4X1VNhOhe1U2E6F7VTYToXtVNhOhe1U2E6F7VTYToXtVNhOhe1U2E6F7VTYToXtVNhOhe1U"
    "2E6F7VTYToXtVNhOhe1U2E61f6PPxesX26mwnQrbqbCdCtupsJ0K26mwnQrbqbCdCtupsJ0K26mwnQrbqbCdCtupsJ0K26mwnQrb"
    "qbCdCtupsJ0K26mwnQrbqbCdCtupsJ0K26mwnQrLqbCcCsupsJwKy6mwnArLqbCcCsupsJwKy6mwnArLqbCcCsupsJwKy6mwnArL"
    "qbCcCsupsJwKy6mwnArLqbCcCsupsJwKy6mwnArLqbCcCsupsJwKy6mwnArLqbCcCsupsJwKy6mwnArLqbCcCsupsJwKy6mwnArL"
    "qbCcCsupsJwKy6mwnArLqbCcCsupsJwKy6mwnAq7qbCbCrupsJsKu6mwmwq7qbCbCrupsJsKu6mwmwq7qbCbCrupsJsKu6mwmwq7"
    "qbCbCrupsJsKu6mwmwq7qbCbCrupjm/0uXj9YjcVdlNhNxV2U2E3FXZTYTcVdlNhNxV2U2E3FXZTYTcVdlNhNxV2U2E3FXZTYTcV"
    "dlNhNxV2U2E3FXZTYTcVdlNhNxV2U2E3FXZTYTcVVlNhNRVWU2E1FVZTYTUVVlNhNRVWU2E1FVZTYTUVVlNhNRVWU2E1FVZTYTUV"
    "VlNhNRVWU2E1FVZTYTUVVlNhNRVWU53e6HPx+sVqKqymwmoqrKbCaiqspsJqKqymwmoqrKbCaipspsJmKmymwmYqbKbCZipspsJm"
    "KmymwmYqbKbCZipspsJmKmymwmYqbKbCZipspsJmKmymwmYqbKbCZipspsJmKmymwmYqLKbCYiospsJiKiymwmIqLKbCYiospsJi"
    "KiymwmIqLKbCYiospsJiKiymwmKq6Y0+F69XLKbCYiospsJiKiymwmIqLKbCYiospsJiKuylwl4q7KXCXirspcJeKuylwl4q7KXC"
    "XirspcJeKuylwl4q7KXCXirspcJeKuylwl4q7KXCXirspcJeKuylwl4q7KXCXirspcJeKuylwl4q7KXCXirspcJeKuylwl4q7KXC"
    "XirspcJeKuylwl4q7KXCXirspcJeKuylwl4q7KXCXirspcJeKuylwl4q7KXCXirspcJeKuylwl4q7KXCXirspbq8cf6n5DPylHxO"
    "viBfkhfvl9hLhb1U2EuFvVTYS4W9VNhLhb1U2EuFvVTYS4W9VNhLhb1U2EuFvVTYS4W9VNhLhb1U2EuFvVTYS4W9VNhLhb1U2EuF"
    "vVTYS4W9VNhLhb1U2EuFvVTYS4W9VNhLhb1U2EuFvVTYS4W9VNhLhb1U2EuFvVTYS4W9VNhLhb1U2EuFvVTYS4W9VNhLhb1U2EuF"
    "vVTYS4W9VNhLhb1U2EuFvVTYS4W9VNhLhb1U2EuFvVTYS4W9VNhLhb1U2EuFvVTYS4W9VFc3zv+MPCWfky/Il+TF+yn2UmEvFfZS"
    "YS8V9lJhLxX2UmEvFfZSYS8V9lJhLxX2UmEvFfZSYS8V9lJhLxX2UmEvFfZSYS8V9lJhLxX2UmEvFfZSYS8V9lJhLxX2UmEvFfZS"
    "YS8V9lJhLxX2UmEvFfZSYS8V9lJhLxX2UmEvFfZSYS8V9lJhLxX2UmEvFfZSYS8V9lJhLxX2UmEvFfZSYS/VZFLfwUq4mtzS3AUD"
    "Y/N/mP87kMmLtpcePefnr+7umXzs3/xT879bH87QO2dy9ddj/veTwxl650yO/r2vzv/OZzhD75zJ5Z/8a/PPo3MmjyY/wvfYH/vS"
    "fzv/XPTGW2eyNLntuQNncvLa/fnnb9cXHidrP9JnsvPBn51/DnTxTPJH+kw+9De/Of885d0xuo7/zvS5bhFG1+x54jC6Fntd+k//"
    "/fk5DaPrnTOZ/oc//8rDntwho2tv8nPPe6w3RldjdM3ywuc4GrOrMbsas6vjxn/f5CXyMnmFvEpeI6+TN8ib5C3yNnmHvEveI++T"
    "D8iH5CPyMfmEfEo+I0/J5+QL8sJz7MbsasyuxuxqzK7G7GrMrsbsasyuxuxqzK7G7GrMrsbsasyuxuxqzK7G7GrMrsbsasyuxuxq"
    "zK7G7GrMrsbsasyuxuxqzK7G7GrMrsbsasyuxuxqzK7G7GrMrsbsasyuxuxqzK7G7GrMrsbsasyuxuxqzK7G7GrMrsbsasyuxuxq"
    "zK7G7GrMrsbsasyuxuxqzK7G7Oq60e/C57Qbs6sxuxqzqzG7GrOrMbsas6sxuxqzqzG7GrOrMbsas6sxuxqzqzG7GrOrMbsas6sx"
    "uxqzqzG7GrOrMbsas6sxuxqzqzG7GrOrMbsas6sxuxqzqzG7GrOrMbsas6sxuxqzqzG7GrOrMbsas6sxuxqzqzG7GrOrMbsas6uX"
    "bvRxRp6Sz8kX5MXrD7OrMbsas6sxuxqzqzG7GrOrMbsas6sxuxqzqzG7GrOrMbsas6sxuxqzqzG7GrOrMbsas6sxuxqzqzG7GrOr"
    "Mbsas6sxuxqzq5dv9Lt4f8XsasyuxuxqzK7G7GrMrsbsasyuxuxqzK7G7GrMrsbsasyuxuxqzK7G7GrMrsbsasyuxuxqzK7G7GrM"
    "rsbsasyuxuxqzK7G7GrMrsbsasyuxuxqzK7G7GrMrsbsasyuxuxqzK7G7GrMrsbsasyuxuxqzK7G7OrVG+d/Sj4jT8nn5Avy4vWG"
    "2dWYXY3Z1ZhdjdnVmF2N2dWYXY3Z1ZhdjdnVmF2N2dWYXY3Z1ZhdjdnVmF2N2dWYXY3Z1ZhdjdnVmF2N2dWYXY3Z1ZhdjdnVazf6"
    "XbyfYnY1ZldjdjVmV2N2NWZXY3Y1ZldjdjVmV2N2NWZXY3Y1ZldjdjVmV2N2NWZXY3Y1ZldjdjVmV2N2NWZXY3Y1ZldjdjVmV2N2"
    "NWZXY3Y1ZldjdjVmV2N2NWZXY3Y1ZldjdjVmV2N2NWZXY3Y1ZldjdjVmV2N2NWZXY3Y1ZldjdjVmV2N2NWZXY3Y1ZldjdjVmV2N2"
    "9caNfhevZ8yuxuxqzK7G7GrMrsbsasyuxuxqzK7G7GrMrsbsasyuxuxqzK7G7GrMrsbsasyuxuxqzK7G7GrMrsbsasyuxuxqzK7G"
    "7GrMrsbsasyuxuxqzK7G7GrMrsbsasyuxuxqzK7G7GrMrsbsasyuxuxqzK7G7GrMrsbsasyuxuxqzK7G7GrMrsbsasyuxuxqzK7G"
    "7GrMrsbsasyuxuxqzK7G7GrMrsbsasyuxuxqzK7G7GrMrsbsasyuxuxqzK7G7GrMrsbsasyu3r7Rxxl5Sj4nX5AvyYv3V8yuxuxq"
    "zK7G7GrMrsbsasyuxuxqzK7G7GrMrsbsasyuxuxqzK7G7GrMrsbsasyuxuxqzK7G7GrMrsbsasyuxuxqzK7G7GrMrsbs6l36xfBq"
    "DK/G8GoMr8bwagyvxvBqDK/G8GoMr8bwagyvxvBqDK/G8GoMrsbgagyuxuBqDK7G4GoMrsbgagyu3r3Rz+L1iMHVGFyNwdUYXI3B"
    "1RhcjcHVGFyNwdUYXI3B1RhcjcHVGFyNwdUYXI3B1RhcjcHVGFyNwdUYXI3B1RhcjcHVGFyNwdUYXI3B1RhcjcE1y0FOcpGbvERe"
    "Jq+QV8lr5HXyBnmTvEXeJu+Qd8l75H3yAfmQfEQ+Jp+QT8ln5Cn5nHxBtt/F6xmTqzG5GpOrMbkak6sxuRqTqzG5GpOrMbkak6sx"
    "uRqTqzG5GpOrMbkak6sxuRqTqzG5GpOrMbkak6sxuRqTqzG5GpOrMbkak6sxuRqTqzG5ZjnISS5yk5fIy+QV8ip5jbxO3iBvkrfI"
    "2+Qd8i55j7xPPiAfko/Ix+QT8in5jDwln5MvyJfkxb4xuhqjqzG6GqOrMboao6sxuhqjqzG6GqOrMboao6sxuhqjqzG6GqOrMboa"
    "o6sxuhqjqzG6GqOrMboao6sxuhqjqzG6GqOrMboao6sxumY5yEkucpOXyMvkFfIqeY28Tt4gb5K3yNvkHfIueY+8Tz4gH5KPyMfk"
    "E/Ip+Yw8JZ+TL8j2u3g9Y3Y1ZldjdjVmV2N2NWZXY3Y1ZldjdjVmV2N2NWZXY3Y1ZldjdjVmV2N2NWZXY3Y1ZldjdjVmV2N2NWZX"
    "Y3Y1ZldjdjVmV2N2NWZXY3bNcpCTXOQmL5GXySvkVfIaeZ28Qd4kb5G3yTvkXfIeeZ98QD4kH5GPySfkU/IZeUo+J1+Q7Xfxesbw"
    "agyvxvBqDK/G8GoMr8bwagyvxvBqDK/G8JrlDfImeYu8Td4h75L3yPvkA/Ih+Yh8TD4hn5LPyFPyOfmCvNg35lRjPjXmU2M+NSZT"
    "YxI1JlFjEDUGUWMKNUZQYwQ1RlBjBDVGUGMENUZQYwQ1RlBjBDVGUGMENUZQYwA1BlBjADUGUGMANQZQYwA1BlBjADUGUGMANQZQ"
    "YwA1BlBjADUGUGMANQZQYwA1BlBjAPXljfM9JZ+Rp+Rz8gX5krx4f8QAasyfxvxpzJ/G+GmMn8b4aYyfxvhpjJ/G+GmMn8b4aYyf"
    "xvhpjJ/G+GmMn8b4aYyfxvhpjJ/G+GmMn8b4aYyfxvhpjJ/G+GmMn8b4aYyfxvhpjJ/G+GmMn8b4aYyfxvhpjJ/G+GmMn8b4aYyf"
    "xvhpjJ/G+GmMn8b4aYyfxvhpjJ/G+GlMn8b0aUyfxvRpTJ/G9GlMn8b0aUyfxvRpTJ/G9GlMn8b0aUyfxvRpTJ/G9GlMn8b0aUyf"
    "xvTpqxvnfUaeks/JF+RL8uL9EdOnMX0a06cxfRrTpzF9GtOnMX0a06cxfRrTpzF9GtOnMX0a06cxfRrTpzF9GtOnMX0a06cxfRrT"
    "pzF9GtOnMX0a06cxfRrTpzF9GsOnMXsas6cxexqjpzF5GpOnMXgag6cxeBqDpzF4GoOnMXgag6cxdxpzpzF3GnOnMXcac6cf3vj7"
    "zd23v/b5vX9Na7znX2H5/N5zv6aVPU/Nnqdmz9PsOcjbX9+9fPr8N+HRc36+f0CS6L0fzrN5/AP+d1eTOzCPf+A3/0XN1Us/iNV3"
    "O/y4rTKCR3i8uHf10fy6eOfbO2TA2tN39fOTJy/xinjrmnj8bd8WD/7xd72W39u89vRXefL2rzb7kfrsPD+5PofV12b3stmJ9Ouz"
    "N6QnX376M69dffaFPgIef5c7wzs//vj6sOPx4uG/yALmvc/ep5oszX7h6/ftmtK5mj0ylt56X696afbbfz5n/3tpcjV/hOSLvQrm"
    "vT99n/rZ+/bsfZ7lx/Hlq/n/eud9jxd4Bk8mr09WZz0/yacvF/PeLM9mlq9en1w/Hp4svf75R69P8sns/83+7wUdwOPZ+/u5x5+d"
    "fOHx0tv58eP5y9eerH3u8ZN3fnzv8RuPr3/qc9f5c49fzBuw9/S3W3z5eP7yjZz9pl/8whdfe5q/cP3zj5+93vWLF3A/+uys0Sev"
    "33i5O3/5xmu7k3j9jSdfXs1nP58xuze9ntePmEdPVt/7WzB7xD+a/bJPHk3i6csJL1+fvPHG5GvzPLt15LUh9Po8zx4g3+E2+YPc"
    "DZ42Xo+/9ri+7RFw/XL38eMHT5a+8JVHr72VZz//ZLI3+drsEXP9376QP59nv8Ta/Lfbm32bv5w8uH75xb2v1BuPa/LaG298rSar"
    "jz43y194nNevV5O9+aPh0Yu5GK+vp9VZm09mt6L5y3l+/TrH2nX+yoO968v/+ueX+q3Xm0S+9fK1F3VLePbO7H+HPPtNVh89/aB7"
    "/9t+fPZh6j+y/4J+/299a2fy5puTb7355vXLJ0/eevksT37p6fdn+aNPvz/78fc8OzuzX/nNd75/4xuL+UtPvz/LX3/6/dmP3wH3"
    "59W/8l/Mn0OcXj9/yD/83s/krbP+1rfe/eWXvv5OfnP28htPnnX4It6CN2e/6te//u4vv/Rt+fqtuH4LrvPf/bvv/S2orNXNyb/1"
    "pH6ztje3Zi8/S/7N2vyrawv5rZf/W63/1a3feDJ5aTPvfDYfXf/o5Nd//atffZa//ede7sx+kz8zfwtm3/7Kr3/5q8/y9fz2S3kL"
    "vtv79fY1cOOu8q1vvZz3+Tv9+K+/pLfg0Qs7u1u+Q51/7mfnfxPa8zvU6uT9mlt9Hv/xt54/vITZ/X6e+d7qGTy+xU/qrF5/hPX4"
    "6dPu96WF+vbw+eu34MH1q+0uPg58rZcz3/Zsbe/t/5V39P6w/Bf+0vz+sHl9f6hvvPzHxhef5PVTmddv78r44uOnz2Vucx7c9htw"
    "/STjFkuY3Zq+/NnJG1/82q3eIx9PvvzFz93i7//g8drtfsr7yezG9PqLe0r7g8xnPj/Zv9VL4frj+K9+dfLm7b0F//FXf/vN/+Cr"
    "O9+6vbdgZ+fXfu36z6Pb7OG234LdT1z9vsnP3brx/m5H8DPLH3/u34CyqbEvvk2w/vrNj6QevevznWd/Hfb9fCD5/d5Ndt/3A771"
    "j7ou/s8/Pm9m/qws+v14rz87mbyITx//AM8Fbm9Wf9geF/EvvDl/XKy+7MfFlyd3ZJ7wrOwOPiLvwONi49/52/N//zu5eRd/Mrnr"
    "c/X36l38Y3/zp+f/qvft7RC7t/Ln2Z06k7/vf/8D8zNZemc7RN/qo+8OnMnP/KF/e/6vUyff+WOwH3zj2vf+Gu/684/e/yVPd6CV"
    "f+Av/8X5v3l82kpc/2ODd/185UtuYbKb839h8KO9Nejv/+2VeSv13a6Vl/SH/upL/vV/yFv5ib8xb2X1O+5BWvqRfKQef/DX5mey"
    "/q47BB/8SJ3Jj/1TPzc/k61nZ7J3659MufUz+Z3//Or8TI7ffpzEj/yZ/MQv/OQrD68/Tp6dSRx/bTJmzJgxY8aMGTNmzJgxY8aM"
    "GTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNm"
    "zJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgx"
    "Y8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aM"
    "GTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNm"
    "zJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgx"
    "Y8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aM"
    "GTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNm"
    "zJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGTNmzJgxY8aMGfN9zuPr"
    "/7c3WZ7U0x/g5cr1y98x2Xhc7/56vv6zl897/Tv38vudGi/fj5ff6+Pv7/mX4/E5+h59j5fj8fOyzuuV+EuvfGWyPvteV5O3vj3N"
    "S698c/LmK1+bfOaVnNz77z49+5GtZ6/xnb79/51m0VE="
)

_STR_TAGS = {0x61: "design", 0x62: "part", 0x63: "date", 0x64: "time"}


def parse_bit(blob):
    pos = 0
    (n,) = struct.unpack_from(">H", blob, pos); pos += 2 + n + 2
    info = {}
    while pos < len(blob):
        tag = blob[pos]; pos += 1
        if tag == 0x65:
            (length,) = struct.unpack_from(">I", blob, pos); pos += 4
            body = blob[pos:pos + length]
            if len(body) != length:
                raise ValueError(f"bitstream truncated: expected {length}, got {len(body)}")
            return info, body
        if tag not in _STR_TAGS:
            raise ValueError(f"unknown tag 0x{tag:02x} at offset {pos - 1}")
        (length,) = struct.unpack_from(">H", blob, pos); pos += 2
        info[_STR_TAGS[tag]] = blob[pos:pos + length].rstrip(b"\x00").decode("latin-1")
        pos += length
    raise ValueError("no configuration payload found")


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "--detect":
        with tempfile.NamedTemporaryFile(suffix=".cfg", mode="w", delete=True) as cfg:
            cfg.write(OPENOCD_CFG); cfg.flush()
            sys.exit(subprocess.run(["openocd", "-f", cfg.name, "-c", "init", "-c", "shutdown"]).returncode)

    if len(sys.argv) != 2 or not sys.argv[1].lower().endswith(".bit"):
        sys.exit(f"Usage: sudo python3 {sys.argv[0]} <fpga.bit | --detect>")

    bit_path = os.path.abspath(sys.argv[1])
    if not os.path.isfile(bit_path):
        sys.exit(f"Error: file not found '{bit_path}'")

    print(f">>> Parsing {os.path.basename(bit_path)} ...")
    with open(bit_path, "rb") as f:
        info, body = parse_bit(f.read())

    print(f"    design : {info.get('design', '?')}")
    print(f"    part   : {info.get('part', '?')}")
    print(f"    built  : {info.get('date', '?')} {info.get('time', '')}")
    print(f"    payload: {len(body)} bytes ({len(body) / 1024:.1f} KiB)")

    part = info.get("part", "").lower()
    if part and "6slx25" not in part:
        print(f"\n[!] Warning: part is '{info['part']}', expected XC6SLX25")

    with tempfile.NamedTemporaryFile(suffix=".cfg", mode="w", delete=True) as cfg, \
         tempfile.NamedTemporaryFile(suffix=".bit", delete=True) as bscan, \
         tempfile.NamedTemporaryFile(suffix=".bin", delete=True) as bin_f:
        cfg.write(OPENOCD_CFG); cfg.flush()
        bscan.write(zlib.decompress(base64.b64decode(_BSCAN_B64))); bscan.flush()
        bin_f.write(body); bin_f.flush()

        print(f"\n>>> Flashing {len(body)} bytes to SPI ...")
        rc = subprocess.run([
            "openocd", "-f", cfg.name,
            "-c", "init",
            "-c", f"pld load 0 {bscan.name}",
            "-c", "reset halt",
            "-c", "flash probe spi0",
            "-c", "flash erase_sector spi0 0 last",
            "-c", f"flash write_bank spi0 {bin_f.name} 0",
            "-c", f"flash verify_bank spi0 {bin_f.name} 0",
            "-c", "virtex2 refresh xc6s.pld",
            "-c", "shutdown",
        ]).returncode

    if rc == 0:
        print("\n>>> Done. FPGA reloaded from SPI flash.")
    else:
        sys.exit(rc)


if __name__ == "__main__":
    main()
