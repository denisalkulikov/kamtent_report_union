# pages/jenkins_advanced.py (исправленная версия со ссылкой на сборку)
import os
import requests
from nicegui import ui
from dotenv import load_dotenv
import asyncio
from datetime import datetime
import re

load_dotenv()


class JenkinsManager:
    def __init__(self):
        self.jenkins_url = os.getenv('JENKINS_URL', 'http://localhost:8080')
        self.username = os.getenv('JENKINS_USERNAME')
        self.password = os.getenv('JENKINS_PASSWORD')
        self.job_name = os.getenv('JENKINS_JOB_NAME', 'union_count_with_download_db_actual')
        self.current_build = None
        self.log_task = None
        self.log_area = None
        self.status_label = None
        self.progress = None
        self.run_btn = None
        self.stop_btn = None
        self.build_link = None  # Добавляем ссылку на сборку
        self.session = None
        self.is_running = False
        self.progress_container = None

    def get_session(self):
        """Получение сессии с CSRF токеном"""
        if self.session is None:
            self.session = requests.Session()
            if self.username and self.password:
                self.session.auth = (self.username, self.password)

            crumb_url = f"{self.jenkins_url}/crumbIssuer/api/json"
            try:
                response = self.session.get(crumb_url, timeout=10)
                if response.status_code == 200:
                    crumb_data = response.json()
                    self.session.headers.update({
                        crumb_data['crumbRequestField']: crumb_data['crumb']
                    })
            except Exception as e:
                print(f"Не удалось получить CSRF токен: {e}")

        return self.session

    def create_ui(self):
        """Создание интерфейса"""
        ui.markdown("## 🚀 Управление Jenkins")
        ui.markdown(f"**Задача:** `{self.job_name}`")

        ui.markdown("""
        > ℹ️ **Важно:** Pipeline содержит этапы с подтверждением загрузки в БД.
        > После запуска сборки, если потребуется подтверждение - оно будет запрошено в веб-интерфейсе Jenkins.
        """).classes('text-caption bg-blue-1 p-2 rounded')

        if not self.username or not self.password:
            ui.label("⚠️ Внимание: Учетные данные Jenkins не настроены в .env файле").classes('text-warning')

        self.status_label = ui.label("Готов к работе").classes('text-grey')

        self.progress_container = ui.column().classes('w-full mt-2')

        ui.markdown("### 📋 Логи сборки")
        self.log_area = ui.log(max_lines=100).classes('w-full h-96')
        self.log_area.clear()

        with ui.row():
            self.run_btn = ui.button("▶️ Запустить сборку", on_click=self.run_build, icon="play_arrow")
            self.stop_btn = ui.button("⏹️ Остановить", on_click=self.stop_tracking, icon="stop")
            self.stop_btn.visible = False
            ui.button("🗑️ Очистить логи", on_click=lambda: self.log_area.clear(), icon="delete")

        # Ссылки
        ui.link("🌐 Открыть Jenkins", self.jenkins_url, new_tab=True)
        # Ссылка на текущую сборку (изначально скрыта)
        self.build_link = ui.link("🔗 Текущая сборка", "#", new_tab=True)
        self.build_link.visible = False

    def show_progress(self, show=True, text=None):
        """Показать/скрыть прогресс"""
        if self.progress_container:
            self.progress_container.clear()
            if show:
                with self.progress_container:
                    with ui.row().classes('items-center gap-4'):
                        progress = ui.circular_progress(
                            value=0,
                            min=0,
                            max=100,
                            size='40px',
                            show_value=False
                        ).props('indeterminate')
                        if text:
                            ui.label(text).classes('text-bold')

    def clean_log_line(self, line):
        """Очистка логов от ANSI кодов и мусора"""
        line = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', line)
        line = re.sub(r'ha:////[^\s]+', '', line)
        line = re.sub(r'\x1b\[[0-9;]*m', '', line)
        line = line.strip()
        return line if line else None

    def add_log(self, message):
        """Безопасное добавление лога"""
        try:
            cleaned = self.clean_log_line(message)
            if cleaned and not cleaned.startswith('[Pipeline]'):
                self.log_area.push(cleaned)
        except:
            pass

    async def run_build(self):
        """Запуск сборки"""
        # Скрываем ссылку на предыдущую сборку
        self.build_link.visible = False

        self.run_btn.disable()
        self.run_btn.loading = True
        self.show_progress(True, "Запуск сборки...")
        self.stop_btn.visible = True
        self.status_label.set_text("⏳ Запуск сборки...")
        self.is_running = True

        try:
            session = self.get_session()
            build_url = f"{self.jenkins_url}/job/{self.job_name}/build"

            self.add_log(f"[{datetime.now().strftime('%H:%M:%S')}] Отправка запроса на запуск...")
            response = session.post(build_url, data={'delay': '0'}, timeout=10)

            if response.status_code == 201 or response.status_code == 200:
                self.add_log(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Сборка запущена успешно")
                self.add_log(
                    f"[{datetime.now().strftime('%H:%M:%S')}] 💡 Если требуется подтверждение - откройте Jenkins в браузере")
                await self.get_build_info()
                asyncio.create_task(self.track_logs())
            elif response.status_code == 403:
                self.add_log(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Ошибка 403: Доступ запрещен")
                self.status_label.set_text("❌ Ошибка авторизации")
                self.cleanup_ui()
            elif response.status_code == 404:
                self.add_log(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Задача '{self.job_name}' не найдена")
                self.status_label.set_text("❌ Задача не найдена")
                self.cleanup_ui()
            else:
                self.add_log(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Ошибка: статус {response.status_code}")
                self.status_label.set_text(f"❌ Ошибка {response.status_code}")
                self.cleanup_ui()

        except Exception as e:
            self.add_log(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Ошибка: {str(e)}")
            self.status_label.set_text("❌ Ошибка подключения")
            self.cleanup_ui()

    async def get_build_info(self):
        """Получение информации о последней сборке"""
        api_url = f"{self.jenkins_url}/job/{self.job_name}/lastBuild/api/json"
        session = self.get_session()

        for attempt in range(10):
            await asyncio.sleep(1)
            if not self.is_running:
                return
            try:
                response = session.get(api_url, timeout=10)
                if response.status_code == 200:
                    self.current_build = response.json()
                    build_number = self.current_build.get('number')
                    self.add_log(f"[{datetime.now().strftime('%H:%M:%S')}] 📦 Номер сборки: #{build_number}")
                    self.status_label.set_text(f"🔄 Сборка #{build_number} выполняется...")

                    # Ссылка на консоль текущей сборки
                    build_console_url = f"{self.jenkins_url}/job/{self.job_name}/{build_number}/console"
                    self.add_log(
                        f"[{datetime.now().strftime('%H:%M:%S')}] 🔗 Следить за прогрессом: {build_console_url}")

                    # Обновляем и показываем ссылку на сборку
                    if self.build_link:
                        self.build_link.visible = True
                        self.build_link.set_text(f"🔗 Сборка #{build_number} (консоль)")
                        # Обновляем href через props
                        self.build_link.props(f"href='{build_console_url}'")
                    return
            except:
                pass

    async def track_logs(self):
        """Отслеживание логов в реальном времени"""
        if not self.current_build:
            return

        build_number = self.current_build['number']
        log_url = f"{self.jenkins_url}/job/{self.job_name}/{build_number}/logText/progressiveText"
        session = self.get_session()
        last_position = 0

        for attempt in range(120):
            if not self.is_running:
                break
            try:
                headers = {'Range': f'bytes={last_position}-'} if last_position > 0 else {}
                response = session.get(log_url, headers=headers, timeout=5)

                if response.status_code in [200, 206]:
                    logs = response.text
                    if logs:
                        for line in logs.split('\n'):
                            cleaned = self.clean_log_line(line)
                            if cleaned:
                                self.add_log(cleaned)

                        if 'content-range' in response.headers:
                            last_position = int(response.headers['content-range'].split('/')[-1])
                        else:
                            last_position += len(logs)

                    if 'Finished:' in logs:
                        if 'SUCCESS' in logs:
                            self.add_log(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Сборка успешно завершена!")
                            self.status_label.set_text("✅ Сборка успешно завершена")
                        elif 'FAILURE' in logs:
                            self.add_log(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Сборка завершена с ошибкой!")
                            self.status_label.set_text("❌ Сборка завершена с ошибкой")
                        elif 'ABORTED' in logs:
                            self.add_log(f"[{datetime.now().strftime('%H:%M:%S')}] ⏹️ Сборка отменена")
                            self.status_label.set_text("⏹️ Сборка отменена")
                        break

                await asyncio.sleep(3)

            except Exception as e:
                self.add_log(f"[{datetime.now().strftime('%H:%M:%S')}] Ошибка получения логов: {str(e)}")
                break

        self.cleanup_ui()

    def stop_tracking(self):
        """Остановка отслеживания логов"""
        self.is_running = False
        self.add_log(f"[{datetime.now().strftime('%H:%M:%S')}] ⏹️ Отслеживание логов остановлено")
        self.cleanup_ui()

    def cleanup_ui(self):
        """Очистка UI после завершения"""
        self.is_running = False
        if self.progress_container:
            try:
                self.progress_container.clear()
            except:
                pass
        self.stop_btn.visible = False
        self.run_btn.enable()
        self.run_btn.loading = False


def create_jenkins_page():
    """Создание страницы Jenkins"""
    manager = JenkinsManager()
    manager.create_ui()