def analyze_multimedia(blocks: List[Any], meta_tags: Dict[str, str]) -> Dict[str, str]:
    res = {
        "primaryImageOfPage": "❌", 
        "mainEntityImage": "❌", 
        "ogImage": str(meta_tags.get("og_image", "❌")), 
        "url_video": "❌ No detectado"
    }
    video_sources = [] 
    main_images = [] # Lista para capturar posibles múltiples imágenes de artículo
    seen_nodes = set()

    def get_url(val):
        if isinstance(val, dict): 
            return val.get("url") or val.get("contentUrl") or val.get("embedUrl")
        return val if isinstance(val, str) else None

    def walk(node: Any):
        if id(node) in seen_nodes: return
        seen_nodes.add(id(node))
        if isinstance(node, dict):
            # 1. primaryImageOfPage
            if "primaryImageOfPage" in node:
                u = get_url(node["primaryImageOfPage"])
                if u: res["primaryImageOfPage"] = str(u)
            
            # 2. mainEntityImage (Article / NewsArticle)
            t = str(node.get("@type", ""))
            if any(at in t for at in ["Article", "NewsArticle", "BlogPosting"]):
                img_data = node.get("image")
                if img_data:
                    if isinstance(img_data, list):
                        for item in img_data:
                            u_img = get_url(item)
                            if u_img: main_images.append(str(u_img))
                    else:
                        u_img = get_url(img_data)
                        if u_img: main_images.append(str(u_img))
            
            # 3. VideoObject
            if "VideoObject" in t:
                u_v = node.get("contentUrl") or node.get("embedUrl") or node.get("url")
                if u_v:
                    u_v_str = str(u_v).lower()
                    if "youtube.com" in u_v_str or "youtu.be" in u_v_str:
                        video_sources.append(f"YouTube ✅ ({u_v})")
                    else:
                        video_sources.append(f"Propio/Otro 🎥 ({u_v})")
            
            for v in node.values(): walk(v)
        elif isinstance(node, list):
            for it in node: walk(it)

    for b in blocks: walk(b)
    
    # ✅ EL ARREGLO CLAVE: Convertimos listas a strings antes de devolver
    if main_images:
        res["mainEntityImage"] = "\n".join(list(dict.fromkeys(main_images)))
        
    if video_sources:
        res["url_video"] = "\n".join(list(dict.fromkeys(video_sources)))
        
    return res
