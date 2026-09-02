# 🌐 WP Multilingual Auto-Translator (Docker)

Servicio autónomo en Docker para la traducción médica continua y sincronización automática del sitio WordPress **neuropediatoolkit.org** con el plugin **TranslatePress**.

## 🚀 Idiomas Soportados
1. **Español (`es_ES`)**: Idioma base original.
2. **Inglés (`en_GB`)**
3. **Alemán (`de_DE`)**
4. **Francés (`fr_FR`)**
5. **Ruso (`ru_RU`)**
6. **Chino Simplificado (`zh_CN`)**
7. **Japonés (`ja`)**

---

## ⚙️ Funcionamiento Autónomo y Cuotas Diarias
* **Ciclos de 24 Horas**: Traduce un cupo controlado de cadenas por cada idioma (configurable en `DAILY_LIMIT_PER_LANG`, por defecto `1000` cadenas/idioma/día).
* **Protección de tasa**: Introduce pausas automáticas con fluctuación (*jitter*) y reintentos exponenciales para evitar bloqueos del traductor.
* **Preservación Médica y Sintáctica**: Utiliza `glossary.json` para estandarizar términos neurológicos críticos, respetando marcadores HTML, URLs y citas bibliográficas.
* **Auto-reinicio / Persistencia**: El contenedor permanece activo (`restart: unless-stopped`), duerme durante el intervalo fijado (24h) y se reactiva automáticamente cada día hasta completar el 100% de las cadenas.

---

## 🛠️ Comandos de Uso

### 1. Iniciar el servicio en segundo plano
```bash
cd /home/arkantu/workspace/wp-multilingual-translator
docker compose up -d --build
```

### 2. Ver logs en tiempo real
```bash
docker compose logs -f
```

### 3. Detener o reiniciar el contenedor
```bash
docker compose down
docker compose restart
```

### 4. Ejecución manual directa (sin Docker)
```bash
python3 translator.py
```
