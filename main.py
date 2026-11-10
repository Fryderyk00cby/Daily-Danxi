"""Start the local web reader; use DanXiDaily.exe for a windowless launch."""
import ctypes
import sys


def main():
    try:
        if sys.platform != 'win32':
            raise RuntimeError('当前版本需要 Windows：登录缓存使用 Windows DPAPI 加密。')
        from app.server import run
        run()
    except Exception as error:
        message = f'树洞日刊启动失败：{error}\n\n请先运行 setup.cmd 安装依赖。也可在项目目录执行 python main.py 查看错误。'
        if sys.stderr is not None:
            print(message, file=sys.stderr)
        if sys.platform == 'win32':
            ctypes.windll.user32.MessageBoxW(None, message, '树洞日刊', 0x10)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
