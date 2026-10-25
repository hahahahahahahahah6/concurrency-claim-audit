from concurrent.futures import ThreadPoolExecutor

def process(items):
    with ThreadPoolExecutor() as pool:
        return list(pool.map(str, items))
