# Playwright официально рекомендует свой базовый образ — в нём уже все системные
# зависимости для Chromium (иначе придётся ставить ~50 либ вручную)
FROM mcr.microsoft.com/playwright:v1.56.0-noble

# Python + curl (curl нужен scheduler-контейнеру для скачивания supercrond)
# python3-pip — нужен для super_basket_vps_system.py (openai + pydantic, GPT-review сигналов)
RUN apt-get update && apt-get install -y python3 python3-pip curl cron --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && pip3 install --no-cache-dir --break-system-packages openai pydantic

COPY package*.json ./
RUN npm ci --omit=dev

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY . .

# Папки для выходных файлов
RUN mkdir -p data state