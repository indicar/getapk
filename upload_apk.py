#!/usr/bin/env python3
"""
Скрипт для загрузки APK файла на сервер.
Запрашивает путь к APK у пользователя и выдает ссылку для скачивания.
"""

import os
import sys
import base64
import requests
from dotenv import load_dotenv


def main():
    # Загрузка переменных окружения из .env файла
    load_dotenv()

    # Получение credentials из переменных окружения
    SERVER_URL = os.getenv('SERVER_URL')
    API_USERNAME = os.getenv('API_USERNAME')
    API_PASSWORD = os.getenv('API_PASSWORD')

    # Проверка наличия необходимых переменных
    if not SERVER_URL:
        print("Ошибка: SERVER_URL не задан в .env файле")
        sys.exit(1)
    if not API_USERNAME or not API_PASSWORD:
        print("Ошибка: API_USERNAME или API_PASSWORD не заданы в .env файле")
        sys.exit(1)

    # Получение пути к APK из аргумента командной строки или запрос у пользователя
    if len(sys.argv) > 1:
        apk_path = sys.argv[1].strip()
    else:
        apk_path = input("Введите путь к APK файлу: ").strip()

    # Проверка существования файла
    if not os.path.exists(apk_path):
        print(f"Ошибка: файл не найден: {apk_path}")
        sys.exit(1)

    # Проверка расширения файла
    if not apk_path.lower().endswith('.apk'):
        print("Предупреждение: файл может не быть APK (нет расширения .apk)")

    print(f"\nЗагрузка файла: {apk_path}")
    print(f"На сервер: {SERVER_URL}\n")

    # Создание заголовка авторизации
    credentials = f"{API_USERNAME}:{API_PASSWORD}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode('utf-8')
    headers = {
        'Authorization': f"Basic {encoded_credentials}"
    }

    # Загрузка файла на сервер
    url = f"{SERVER_URL}/upload"

    try:
        with open(apk_path, 'rb') as file:
            files = {
                'file': (os.path.basename(apk_path), file, 'application/vnd.android.package-archive')
            }
            response = requests.post(url, files=files, headers=headers)

        if response.status_code == 200:
            result = response.json()
            print("✅ Файл успешно загружен!\n")
            print(f"Имя файла: {result.get('filename')}")
            print(f"\nПостоянная ссылка (с авторизацией): {SERVER_URL}/download")
            print(f"Временная ссылка: {result.get('public_url')}")
            print(f"Временная ссылка действительна до: {result.get('expires_at')}")
        elif response.status_code == 401:
            print("Ошибка: неверные учетные данные (401 Unauthorized)")
            sys.exit(1)
        else:
            print(f"Ошибка загрузки: {response.status_code}")
            print(response.text)
            sys.exit(1)

    except requests.exceptions.ConnectionError:
        print(f"Ошибка: не удалось подключиться к серверу {SERVER_URL}")
        sys.exit(1)
    except Exception as e:
        print(f"Ошибка: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()