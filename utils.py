import os

def get_master_key():
    key = os.environ.get("NSR_MASTER_KEY")
    if not key:
        raise RuntimeError("NSR_MASTER_KEY no está configurada en las variables de entorno")
    return key

def obfuscate_url(url: str, key: str = None) -> str:
    """
    Ofusca una URL sumando los valores Unicode usando una llave.
    Retorna un string hexadecimal para asegurar transmisión segura en JSON.
    """
    if not url:
        return url
    
    key = key or get_master_key()
    key_len = len(key)
    res = []
    
    for i, char in enumerate(url):
        shift = ord(key[i % key_len])
        new_char = ord(char) + shift
        res.append(format(new_char, '04x'))
        
    return "".join(res)

def deobfuscate_url(hex_str: str, key: str = None) -> str:
    if not hex_str:
        return hex_str
        
    key = key or get_master_key()
    key_len = len(key)
    res = ""
    
    for i in range(0, len(hex_str), 4):
        char_code = int(hex_str[i:i+4], 16)
        shift = ord(key[(i // 4) % key_len])
        res += chr(char_code - shift)
        
    return res
