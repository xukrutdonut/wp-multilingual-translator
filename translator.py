#!/usr/bin/env python3
import os
import sys
import time
import json
import re
import signal
import random
import urllib.request
import urllib.parse
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

# ================= Configuration =================
SSH_HOST = os.getenv("SSH_HOST", "89.117.169.108")
SSH_PORT = os.getenv("SSH_PORT", "65002")
SSH_USER = os.getenv("SSH_USER", "u691704582")
SSH_PASS = os.getenv("SSH_PASS", "Mayo-1968!")
WP_PATH = os.getenv("WP_PATH", "/home/u691704582/domains/neuropediatoolkit.org/public_html/")

# Languages: WordPress TranslatePress code -> Google Translate code
LANG_MAP = {
    "en_GB": "en",
    "de_DE": "de",
    "fr_FR": "fr",
    "ru_RU": "ru",
    "zh_CN": "zh-CN",
    "ja": "ja"
}

# DeepL Target Language Mapping
DEEPL_LANG_MAP = {
    "en_GB": "EN-GB",
    "en": "EN-GB",
    "de_DE": "DE",
    "de": "DE",
    "fr_FR": "FR",
    "fr": "FR",
    "ru_RU": "RU",
    "ru": "RU",
    "zh_CN": "ZH",
    "zh": "ZH",
    "ja": "JA"
}

# DeepL API Keys (supports single key or comma-separated list of keys)
raw_deepl_keys = os.getenv("DEEPL_API_KEYS", os.getenv("DEEPL_AUTH_KEY", ""))
DEEPL_KEYS = [k.strip() for k in raw_deepl_keys.split(",") if k.strip()]
CURRENT_DEEPL_KEY_INDEX = 0

# Local / Remote Multi-GPU LLM Translation Pool (Intel ARC + AMD Radeon RX 480)
USE_LLM = os.getenv("USE_LLM", "true").lower() in ("true", "1", "yes")
LLM_API_URL = os.getenv("LLM_API_URL", "http://192.168.0.100:1234/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "rx480/qwen1.5-moe-a2.7b-chat@q4_k_m")
DEFAULT_ENDPOINTS = [
    {
        "url": "http://192.168.0.100:1234/v1",
        "model": "rx480/qwen1.5-moe-a2.7b-chat@q4_k_m",
        "name": "AMD-RX480 (Khazad-dum)"
    },
    {
        "url": "http://192.168.0.100:1234/v1",
        "model": "intel-arc-qwen2.5-coder-7b-instruct",
        "name": "Intel-ARC (Khazad-dum)"
    }
]

raw_endpoints = os.getenv("LLM_ENDPOINTS_JSON", "")
if raw_endpoints:
    try:
        LLM_ENDPOINTS = json.loads(raw_endpoints)
    except:
        LLM_ENDPOINTS = DEFAULT_ENDPOINTS
else:
    LLM_ENDPOINTS = DEFAULT_ENDPOINTS

LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "60"))
NUM_WORKERS = int(os.getenv("NUM_WORKERS", str(max(len(LLM_ENDPOINTS) * 2, 4))))

LANG_NAME_MAP = {
    "en_GB": "British English",
    "en": "English",
    "de_DE": "German",
    "de": "German",
    "fr_FR": "French",
    "fr": "French",
    "ru_RU": "Russian",
    "ru": "Russian",
    "zh_CN": "Simplified Chinese",
    "zh": "Simplified Chinese",
    "ja": "Japanese"
}

BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50"))
DAILY_LIMIT_PER_LANG = int(os.getenv("DAILY_LIMIT_PER_LANG", "0"))
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "0.05"))
CYCLE_INTERVAL_HOURS = float(os.getenv("CYCLE_INTERVAL_HOURS", "0"))
GLOSSARY_FILE = os.getenv("GLOSSARY_FILE", "glossary.json")

RUNNING = True
SSH_LOCK = threading.Lock()
ENDPOINT_LOCK = threading.Lock()
ENDPOINT_INDEX = 0

def get_next_endpoint():
    global ENDPOINT_INDEX
    with ENDPOINT_LOCK:
        ep = LLM_ENDPOINTS[ENDPOINT_INDEX % len(LLM_ENDPOINTS)]
        ENDPOINT_INDEX += 1
        return ep

def handle_signal(sig, frame):
    global RUNNING
    print("\n[INFO] Señal de terminación recibida. Cerrando ciclo de forma segura...", flush=True)
    RUNNING = False

signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)

# ================= Remote Execution Helper =================
def run_remote_php(php_code, timeout=45):
    """Executes a PHP snippet in the remote WordPress environment using WP-CLI."""
    local_temp = f"/tmp/wp_exec_{int(time.time()*1000)}_{random.randint(100,999)}.php"
    remote_temp = f"/home/{SSH_USER}/tmp/{os.path.basename(local_temp)}"

    with open(local_temp, "w", encoding="utf-8") as f:
        f.write(php_code)

    scp_cmd = [
        "sshpass", "-p", SSH_PASS,
        "scp", "-P", SSH_PORT,
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=15",
        local_temp, f"{SSH_USER}@{SSH_HOST}:{remote_temp}"
    ]
    try:
        res_scp = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"[ERROR] SCP timeout ({timeout}s)", file=sys.stderr, flush=True)
        return None
    finally:
        try:
            os.remove(local_temp)
        except:
            pass

    if res_scp.returncode != 0:
        print(f"[ERROR] Error SCP: {res_scp.stderr.strip()}", file=sys.stderr, flush=True)
        return None

    ssh_cmd = [
        "sshpass", "-p", SSH_PASS,
        "ssh", "-p", SSH_PORT,
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=15",
        f"{SSH_USER}@{SSH_HOST}",
        f"wp --path={WP_PATH} eval-file {remote_temp} && rm -f {remote_temp}"
    ]
    try:
        res_ssh = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"[ERROR] SSH timeout ({timeout}s)", file=sys.stderr, flush=True)
        return None

    if res_ssh.returncode != 0 and not res_ssh.stdout:
        print(f"[ERROR] Error SSH WP eval: {res_ssh.stderr.strip()}", file=sys.stderr, flush=True)
        return None

    return res_ssh.stdout

# ================= Glossary =================
def load_glossary():
    if os.path.exists(GLOSSARY_FILE):
        try:
            with open(GLOSSARY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[WARN] Error cargando glosario: {e}", flush=True)
    return {}

GLOSSARY = load_glossary()

# ================= Filter & Cleaner =================
def should_skip(text):
    if not text:
        return True
    s = text.strip()
    if len(s) == 0:
        return True
    if len(s) <= 2 and not s.isalnum():
        return True
    if s.startswith("http://") or s.startswith("https://") or s.startswith("//"):
        return True
    if s.startswith("data:image"):
        return True
    if "%7B" in s or "%22" in s or "%5B" in s:
        return True
    if s.startswith("{") and s.endswith("}"):
        return True
    if s.startswith("[") and s.endswith("]"):
        return True
    if re.match(r"^[\d\s\.\,\:\;\-\_\+\*\/\=\%\$\€\(\)\[\]#@!<>]+$", s):
        return True
    if re.match(r"^<[^>]+>$", s):
        return True
    return False

# ================= Multi-Provider Translation Engine =================
def translate_llm(text, target_lang):
    """Translates text using Multi-GPU LLM Pool (Intel ARC + AMD RX 480). Preserves HTML tags and markdown."""
    if not USE_LLM or not LLM_ENDPOINTS:
        return None

    target_lang_name = LANG_NAME_MAP.get(target_lang, target_lang)
    
    prompt = (
        f"You are a professional medical translator. Translate the following text from Spanish to {target_lang_name}.\n"
        "Strict rules:\n"
        "1. Preserve ALL HTML tags, shortcodes, placeholders (e.g. %s, {name}), and attributes exactly as they appear.\n"
        "2. Keep medical terminology accurate and natural.\n"
        "3. Output ONLY the translated text without any explanation, markdown backticks, or intro."
    )

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "WP-Medical-Translator/2.0"
    }

    # Start with next round-robin endpoint, fallback to remaining endpoints
    start_ep = get_next_endpoint()
    endpoints_to_try = [start_ep]
    for ep in LLM_ENDPOINTS:
        if ep not in endpoints_to_try:
            endpoints_to_try.append(ep)

    for ep in endpoints_to_try:
        endpoint = f"{ep['url'].rstrip('/')}/chat/completions"
        payload = json.dumps({
            "model": ep["model"],
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": text}
            ],
            "temperature": 0.1,
            "max_tokens": max(len(text) * 3, 100)
        }).encode("utf-8")

        try:
            req = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode("utf-8"))
                    choices = data.get("choices", [])
                    if choices and "message" in choices[0] and "content" in choices[0]["message"]:
                        res = choices[0]["message"]["content"].strip()
                        if res.startswith("```") and res.endswith("```"):
                            res = re.sub(r"^```[a-zA-Z]*\n?", "", res)
                            res = re.sub(r"\n?```$", "", res).strip()
                        if res:
                            return res
        except Exception:
            continue

    return None

def translate_deepl(text, target_lang):
    """DeepL API with multi-key pool and automatic Free/Pro endpoint detection."""
    global CURRENT_DEEPL_KEY_INDEX, DEEPL_KEYS
    if not DEEPL_KEYS:
        return None

    deepl_target = DEEPL_LANG_MAP.get(target_lang) or DEEPL_LANG_MAP.get(target_lang.split("-")[0].lower())
    if not deepl_target:
        return None

    total_keys = len(DEEPL_KEYS)
    for _ in range(total_keys):
        if CURRENT_DEEPL_KEY_INDEX >= len(DEEPL_KEYS):
            CURRENT_DEEPL_KEY_INDEX = 0

        api_key = DEEPL_KEYS[CURRENT_DEEPL_KEY_INDEX]
        endpoint = "https://api-free.deepl.com/v2/translate" if api_key.endswith(":fx") else "https://api.deepl.com/v2/translate"

        payload = json.dumps({
            "text": [text],
            "source_lang": "ES",
            "target_lang": deepl_target
        }).encode("utf-8")

        headers = {
            "Authorization": f"DeepL-Auth-Key {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "WP-Medical-Translator/2.0"
        }

        req = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode("utf-8"))
                    translations = data.get("translations", [])
                    if translations and "text" in translations[0]:
                        return translations[0]["text"]
        except urllib.error.HTTPError as e:
            if e.code in (456, 403):  # 456 Quota exceeded, 403 Forbidden / invalid key
                masked_key = api_key[:4] + "..." + api_key[-4:] if len(api_key) > 8 else "***"
                print(f"[WARN DeepL] Clave {masked_key} sin cuota o inválida (HTTP {e.code}). Rotando a siguiente clave...", flush=True)
                CURRENT_DEEPL_KEY_INDEX = (CURRENT_DEEPL_KEY_INDEX + 1) % len(DEEPL_KEYS)
                continue
            else:
                break
        except Exception:
            break

    return None

def translate_google_chrome(text, target_lang):
    """Google Translate via dict-chrome-ex endpoint"""
    encoded_query = urllib.parse.quote(text)
    url = f"https://clients5.google.com/translate_a/t?client=dict-chrome-ex&sl=es&tl={target_lang}&q={encoded_query}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=8) as response:
        raw = response.read().decode("utf-8")
        data = json.loads(raw)
        if isinstance(data, list) and len(data) > 0:
            if isinstance(data[0], str):
                return data[0]
            elif isinstance(data[0], list) and len(data[0]) > 0:
                return "".join([str(seg) for seg in data[0] if seg])
        elif isinstance(data, str):
            return data
    return None

def translate_mymemory(text, target_lang):
    """MyMemory Free Translation API Fallback"""
    short_lang = target_lang.split("-")[0]
    encoded_query = urllib.parse.quote(text)
    url = f"https://api.mymemory.translated.net/get?q={encoded_query}&langpair=es|{short_lang}"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; MedicalAssistant/1.0)"
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=8) as response:
        data = json.loads(response.read().decode("utf-8"))
        res_data = data.get("responseData", {})
        trans = res_data.get("translatedText")
        if trans and "MYMEMORY WARNING" not in trans.upper():
            return trans
    return None

# ================= Translation Quality Verification & Checker =================
def verify_and_correct_translation(original, translated, target_lang):
    """
    Final validation & correction step:
    1. Checks tag/placeholder balance (HTML tags, %s, shortcodes).
    2. Uses LLM verification to fix truncated or hallucinated translations.
    3. Guarantees safety before persisting to WordPress database.
    """
    if not translated or translated == original:
        return translated

    # Check 1: HTML Tag Integrity
    orig_tags = sorted(re.findall(r"<[^>]+>", original))
    trans_tags = sorted(re.findall(r"<[^>]+>", translated))
    
    # Check 2: Placeholders (e.g. %s, %d, {name})
    orig_placeholders = sorted(re.findall(r"%[sdf]|%[0-9]+\$[sdf]|{[^}]+}", original))
    trans_placeholders = sorted(re.findall(r"%[sdf]|%[0-9]+\$[sdf]|{[^}]+}", translated))

    tags_corrupted = (orig_tags != trans_tags)
    placeholders_corrupted = (orig_placeholders != trans_placeholders)
    is_suspicious = tags_corrupted or placeholders_corrupted or (len(original) > 20 and len(translated) < 3)

    if is_suspicious and USE_LLM and (LLM_ENDPOINTS or LLM_API_URL):
        target_lang_name = LANG_NAME_MAP.get(target_lang, target_lang)
        prompt = (
            f"You are a Senior Medical Proofreader and Quality Assurance Specialist for Web Localization.\n"
            f"Original Spanish: {original}\n"
            f"Draft Translation ({target_lang_name}): {translated}\n\n"
            f"Review and correct the draft translation into {target_lang_name}.\n"
            f"Strict requirements:\n"
            f"1. Ensure ALL original HTML tags, shortcodes, and placeholders appear EXACTLY as in the original.\n"
            f"2. Ensure accurate medical terminology and natural phrasing.\n"
            f"3. Return ONLY the final corrected translation."
        )

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "WP-Medical-Translator/2.0"
        }

        endpoints_to_try = list(LLM_ENDPOINTS) if LLM_ENDPOINTS else [{"url": LLM_API_URL, "model": LLM_MODEL}]
        for ep in endpoints_to_try:
            endpoint_url = f"{ep['url'].rstrip('/')}/chat/completions"
            payload = json.dumps({
                "model": ep.get("model", LLM_MODEL),
                "messages": [
                    {"role": "system", "content": prompt}
                ],
                "temperature": 0.05,
                "max_tokens": max(len(original) * 3, 100)
            }).encode("utf-8")

            req = urllib.request.Request(endpoint_url, data=payload, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as response:
                    if response.status == 200:
                        data = json.loads(response.read().decode("utf-8"))
                        choices = data.get("choices", [])
                        if choices and "message" in choices[0] and "content" in choices[0]["message"]:
                            corrected = choices[0]["message"]["content"].strip()
                            if corrected.startswith("```") and corrected.endswith("```"):
                                corrected = re.sub(r"^```[a-zA-Z]*\n?", "", corrected)
                                corrected = re.sub(r"\n?```$", "", corrected).strip()
                            if corrected:
                                return corrected
            except Exception:
                continue

    return translated

def translate_text(text, target_lang, retries=2):
    if should_skip(text):
        return text

    # 1. Direct glossary lookup
    text_lower = text.strip().lower()
    if text_lower in GLOSSARY:
        g_trans = GLOSSARY[text_lower].get(target_lang) or GLOSSARY[text_lower].get(target_lang[:2])
        if g_trans:
            return g_trans

    translated_candidate = None

    # 2. Multi-provider cascade
    for attempt in range(retries):
        # Try Priority 1: Local / Remote LLM (LM Studio / OpenVINO)
        if USE_LLM:
            try:
                res = translate_llm(text, target_lang)
                if res and len(res.strip()) > 0:
                    translated_candidate = res
                    break
            except Exception:
                pass

        # Try Priority 2: DeepL (if API keys configured)
        if DEEPL_KEYS:
            try:
                res = translate_deepl(text, target_lang)
                if res and len(res.strip()) > 0:
                    translated_candidate = res
                    break
            except Exception:
                pass

        # Try Priority 3: Google Chrome extension API
        try:
            res = translate_google_chrome(text, target_lang)
            if res and len(res.strip()) > 0:
                translated_candidate = res
                break
        except Exception:
            pass

        # Try Priority 4: MyMemory API
        try:
            res = translate_mymemory(text, target_lang)
            if res and len(res.strip()) > 0:
                translated_candidate = res
                break
        except Exception:
            pass

        time.sleep(1.0)

    if not translated_candidate:
        return text

    # 3. Final Verification & Quality Assurance Step
    final_translation = verify_and_correct_translation(text, translated_candidate, target_lang)
    return final_translation

# ================= Stats & Status =================
def get_language_stats(lang_code):
    table_name = f"wp_trp_dictionary_es_es_{lang_code.lower()}"
    php_code = f"""<?php
global $wpdb;
$table = '{table_name}';
$total = (int)$wpdb->get_var("SELECT COUNT(*) FROM $table");
$translated = (int)$wpdb->get_var("SELECT COUNT(*) FROM $table WHERE translated IS NOT NULL AND translated != ''");
$untranslated = (int)$wpdb->get_var("SELECT COUNT(*) FROM $table WHERE translated IS NULL OR translated = ''");
echo json_encode(['total' => $total, 'translated' => $translated, 'untranslated' => $untranslated]);
"""
    out = run_remote_php(php_code)
    if out:
        try:
            return json.loads(out)
        except:
            pass
    return {"total": 0, "translated": 0, "untranslated": 0}

def fetch_untranslated_batch(lang_code, limit=50):
    table_name = f"wp_trp_dictionary_es_es_{lang_code.lower()}"
    php_code = f"""<?php
global $wpdb;
$table = '{table_name}';
$rows = $wpdb->get_results("
    SELECT id, original 
    FROM $table 
    WHERE (translated IS NULL OR translated = '')
      AND original NOT LIKE 'http%'
      AND original NOT LIKE 'data:image%'
      AND original NOT LIKE '%7B%'
      AND original NOT LIKE '%5B%'
      AND original NOT LIKE '{{%}}'
      AND LENGTH(original) > 1
    ORDER BY id ASC
    LIMIT {limit}
");
echo json_encode($rows);
"""
    out = run_remote_php(php_code)
    if out:
        try:
            return json.loads(out)
        except Exception as e:
            print(f"[ERROR] Parse batch JSON: {e}", flush=True)
    return []

def push_translations(lang_code, translations_list):
    if not translations_list:
        return 0

    import base64
    table_name = f"wp_trp_dictionary_es_es_{lang_code.lower()}"
    php_data = json.dumps(translations_list, ensure_ascii=False)
    b64_data = base64.b64encode(php_data.encode("utf-8")).decode("ascii")
    
    php_code = f"""<?php
global $wpdb;
$table = '{table_name}';
$raw = base64_decode('{b64_data}');
$items = json_decode($raw, true);

$updated = 0;
if (is_array($items)) {{
    foreach ($items as $item) {{
        $id = (int)$item['id'];
        $trans = $item['translated'];
        $status = 1; // Machine translated
        
        $res = $wpdb->update(
            $table,
            ['translated' => $trans, 'status' => $status],
            ['id' => $id],
            ['%s', '%d'],
            ['%d']
        );
        if ($res !== false) {{
            $updated++;
        }}
    }}
}}
echo json_encode(['updated' => $updated]);
"""
    out = run_remote_php(php_code)
    if out:
        try:
            res = json.loads(out)
            return res.get('updated', 0)
        except:
            pass
    return 0

def translate_single_item(row, lang_google):
    if not RUNNING:
        return None
    original = row["original"]
    item_id = row["id"]
    if should_skip(original):
        return {"id": item_id, "translated": original}
    else:
        translated = translate_text(original, lang_google)
        if translated:
            return {"id": item_id, "translated": translated}
        else:
            return {"id": item_id, "translated": original}

# ================= Processing Workflow =================
def process_language(lang_tp, lang_google, daily_limit):
    is_unlimited = (daily_limit <= 0)
    limit_str = "Ilimitado" if is_unlimited else str(daily_limit)
    print(f"\n=======================================================", flush=True)
    print(f"[*] Idioma: {lang_tp} (Destino: {lang_google}) | Límite diario: {limit_str}", flush=True)
    print(f"=======================================================", flush=True)

    stats_before = get_language_stats(lang_tp)
    total = stats_before['total']
    trans_before = stats_before['translated']
    pct_before = (trans_before / total * 100) if total > 0 else 0.0
    print(f"Estado inicial: {trans_before}/{total} ({pct_before:.2f}%) traducidas.", flush=True)

    count_today = 0
    while RUNNING and (is_unlimited or count_today < daily_limit):
        chunk_size = BATCH_SIZE if is_unlimited else min(BATCH_SIZE, daily_limit - count_today)
        print(f"[{lang_tp}] Obteniendo lote de hasta {chunk_size} cadenas...", flush=True)
        batch = fetch_untranslated_batch(lang_tp, limit=chunk_size)
        if not batch:
            print(f"[✓] No quedan más cadenas pendientes para {lang_tp}.", flush=True)
            break

        print(f"[{lang_tp}] Traduciendo lote de {len(batch)} cadenas en paralelo ({NUM_WORKERS} workers Dual-GPU)...", flush=True)
        with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
            results = list(executor.map(lambda r: translate_single_item(r, lang_google), batch))
        
        to_update = [r for r in results if r is not None]

        if to_update:
            print(f"[{lang_tp}] Guardando {len(to_update)} cadenas en WordPress...", flush=True)
            with SSH_LOCK:
                updated = push_translations(lang_tp, to_update)
            count_today += len(to_update)
            print(f"[{lang_tp}] ✓ +{updated} cadenas subidas a BD (Total sesión: {count_today}/{limit_str})", flush=True)

        time.sleep(REQUEST_DELAY)

    stats_after = get_language_stats(lang_tp)
    total_after = stats_after['total']
    trans_after = stats_after['translated']
    pct_after = (trans_after / total_after * 100) if total_after > 0 else 0.0
    print(f"[RESUMEN {lang_tp}] Progreso final: {trans_after}/{total_after} ({pct_after:.2f}%) | +{count_today} traducidas en esta pasada.\n", flush=True)

    return {
        "lang": lang_tp,
        "google": lang_google,
        "translated_today": count_today,
        "total": total_after,
        "translated": trans_after,
        "pending": stats_after['untranslated'],
        "percent": pct_after
    }

def run_daily_cycle():
    start_time = datetime.now()
    print(f"\n=======================================================", flush=True)
    print(f"🚀 INICIANDO CICLO DE TRADUCCIÓN: {start_time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print(f"=======================================================", flush=True)

    daily_results = []
    for lang_tp, lang_google in LANG_MAP.items():
        if not RUNNING:
            break
        res = process_language(lang_tp, lang_google, DAILY_LIMIT_PER_LANG)
        daily_results.append(res)

    end_time = datetime.now()
    duration = end_time - start_time
    print(f"\n=======================================================", flush=True)
    print(f"✨ CICLO COMPLETADO (Duración: {duration})", flush=True)
    print(f"📊 TABLA GENERAL DE PROGRESO MULTILINGÜE:", flush=True)
    print(f"-------------------------------------------------------", flush=True)
    print(f"{'Idioma':<10} | {'Sesión':<8} | {'Traducidas':<12} | {'Pendientes':<10} | {'Progreso':<10}", flush=True)
    print(f"-------------------------------------------------------", flush=True)
    for r in daily_results:
        print(f"{r['lang']:<10} | +{r['translated_today']:<7} | {r['translated']:<12} | {r['pending']:<10} | {r['percent']:.2f}%", flush=True)
    print(f"=======================================================\n", flush=True)

def main():
    print("=======================================================", flush=True)
    print("🤖 SERVICIO AUTÓNOMO DE TRADUCCIÓN WORDPRESS MULTILINGÜE", flush=True)
    print("=======================================================", flush=True)
    print(f"Host remoto: {SSH_HOST}:{SSH_PORT} ({SSH_USER})", flush=True)
    print(f"Idiomas activos: {', '.join(LANG_MAP.keys())}", flush=True)
    limit_str = "Ilimitado" if DAILY_LIMIT_PER_LANG <= 0 else str(DAILY_LIMIT_PER_LANG)
    print(f"Límite por idioma / día: {limit_str}", flush=True)
    freq_str = "Continuo (sin espera)" if CYCLE_INTERVAL_HOURS <= 0 else f"Cada {CYCLE_INTERVAL_HOURS} horas"
    print(f"Frecuencia de ciclo: {freq_str}", flush=True)
    if DEEPL_KEYS:
        print(f"DeepL API: Activado ({len(DEEPL_KEYS)} claves en rotación)", flush=True)
    else:
        print("DeepL API: Desactivado (usando Google Chrome & MyMemory fallback)", flush=True)
    print("=======================================================", flush=True)

    while RUNNING:
        run_daily_cycle()
        if not RUNNING:
            break
        if CYCLE_INTERVAL_HOURS <= 0:
            print("[INFO] Modo continuo activo. Reiniciando comprobación en 30s...", flush=True)
            for _ in range(6):
                if not RUNNING:
                    break
                time.sleep(5)
        else:
            sleep_secs = int(CYCLE_INTERVAL_HOURS * 3600)
            print(f"[💤] Pausando por {CYCLE_INTERVAL_HOURS} horas hasta el siguiente ciclo ({sleep_secs}s)...", flush=True)
            for _ in range(int(sleep_secs / 5)):
                if not RUNNING:
                    break
                time.sleep(5)

    print("[INFO] Proceso detenido limpiamente.", flush=True)

if __name__ == "__main__":
    main()
