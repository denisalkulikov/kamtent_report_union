# main.py
from nicegui import ui
from dotenv import load_dotenv
from pages.jenkins_advanced import create_jenkins_page
from pages.excel_parser_page import create_excel_parser_page
from pages.monthly_report_page import create_monthly_report_page
from pages.forecasting_page import create_forecasting_page

# Загружаем переменные окружения ПЕРЕД всем остальным
load_dotenv()

# Остальной код без изменений...
tabs_content = {
    "Jenkins": create_jenkins_page,
    "Excel Парсер": create_excel_parser_page,
    "Ежемесячный отчёт": create_monthly_report_page,
    "Прогнозирование": create_forecasting_page,
}


@ui.page('/')
def main_page():
    with ui.tabs().classes('w-full') as tabs:
        for name in tabs_content.keys():
            ui.tab(name)

    with ui.tab_panels(tabs, value='Jenkins').classes('w-full'):
        for name, content_func in tabs_content.items():
            with ui.tab_panel(name):
                content_func()


if __name__ == "__main__":
    ui.run(
        title="Объединенные отчеты и инструменты",
        favicon="🚀",
        port=8001,
        reload=False
    )