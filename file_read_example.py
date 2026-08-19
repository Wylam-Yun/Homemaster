def read_file_content(file_path):
    """
    读取文件的全部内容。

    参数:
        file_path (str): 要读取的文件路径。

    返回:
        str: 文件内容，如果发生错误则返回 None。
    """
    try:
        with open(file_path, encoding='utf-8') as file:
            content = file.read()
            return content
    except FileNotFoundError:
        print(f"错误：未找到路径为 {file_path} 的文件。")
    except Exception as e:
        print(f"发生意外错误：{e}")
    return None

if __name__ == "__main__":
    # 示例用法
    file_path = "example.txt"

    # 创建一个演示用的临时文件
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("你好，这是一个文件读取示例。\n这是文件的第二行。")

    # 读取并打印文件内容
    content = read_file_content(file_path)

    if content:
        print("--- 文件内容 ---")
        print(content)
        print("---------------------")
