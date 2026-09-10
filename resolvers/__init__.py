from .voe import extract_voe
from .streamwish import extract_streamwish
from .streamtape import extract_streamtape

async def resolve_url(engine_session, embed_url, fetch_html_func):
    """
    Toma un enlace embed y detecta automáticamente qué resolutor usar.
    Retorna la URL directa del video (.mp4, .m3u8) o None si falla.
    """
    url_lower = embed_url.lower()
    
    if "voe.sx" in url_lower or "voesx" in url_lower:
        return await extract_voe(engine_session, embed_url, fetch_html_func)
        
    elif "streamwish" in url_lower or "wish" in url_lower:
        return await extract_streamwish(engine_session, embed_url, fetch_html_func)
        
    elif "streamtape" in url_lower or "tape" in url_lower:
        return await extract_streamtape(engine_session, embed_url, fetch_html_func)
        
    else:
        # En caso de no haber un resolutor específico, se podría 
        # intentar un extractor genérico (yt-dlp) en el futuro.
        return None
