# pages/excel_parser_page.py
from nicegui import ui
import pandas as pd
from io import BytesIO
import asyncio
import datetime


def create_excel_parser_page():
    """Создание страницы Excel парсера"""

    # Инициализируем состояние, если его нет
    if not hasattr(ui.state, 'excel_parser_all_data'):
        ui.state.excel_parser_all_data = []
    if not hasattr(ui.state, 'excel_parser_current_filename'):
        ui.state.excel_parser_current_filename = None

    def detect_file_structure(df, start_row=9):
        """Определяет структуру файла: где находятся суммы"""
        structure = {
            'payer_cols': [3, 4],
            'sum_col': None,
            'has_merged_f_g': False,
            'has_merged_g_h': False,
        }

        print(f"\n=== Анализ структуры файла ===")

        col_stats = {5: {'values': [], 'is_number': False},
                     6: {'values': [], 'is_number': False},
                     7: {'values': [], 'is_number': False}}

        for offset in range(min(5, len(df) - start_row)):
            row_idx = start_row + offset
            if row_idx >= len(df):
                break

            print(f"\nСтрока {row_idx + 1}:")

            for col in [5, 6, 7]:
                if col < len(df.columns):
                    val = df.iloc[row_idx, col]
                    print(f"  {chr(65 + col)}: {val}")

                    if pd.notna(val):
                        val_str = str(val).strip()
                        col_stats[col]['values'].append(val_str)

                        try:
                            float(val_str.replace(',', '.'))
                            col_stats[col]['is_number'] = True
                        except:
                            pass

            f_val = df.iloc[row_idx, 5] if 5 < len(df.columns) else None
            g_val = df.iloc[row_idx, 6] if 6 < len(df.columns) else None
            h_val = df.iloc[row_idx, 7] if 7 < len(df.columns) else None

            if pd.notna(f_val) and pd.notna(g_val) and str(f_val) == str(g_val):
                structure['has_merged_f_g'] = True
                print(f"  Обнаружено объединение F и G")

            if pd.notna(g_val) and pd.notna(h_val) and str(g_val) == str(h_val):
                structure['has_merged_g_h'] = True
                print(f"  Обнаружено объединение G и H")

        print(f"\n=== Статистика по столбцам ===")

        if structure['has_merged_f_g']:
            structure['sum_col'] = 5
            print(f"Объединены F и G -> используем F")
        elif structure['has_merged_g_h']:
            structure['sum_col'] = 6
            print(f"Объединены G и H -> используем G")
        else:
            number_cols = []

            for col, stats in col_stats.items():
                if stats['is_number'] and stats['values']:
                    all_numbers = True
                    for val in stats['values']:
                        try:
                            float(val.replace(',', '.'))
                        except:
                            all_numbers = False
                            break

                    if all_numbers:
                        number_cols.append(col)
                        print(f"Столбец {chr(65 + col)}: все значения - числа ({len(stats['values'])} значений)")
                    else:
                        print(f"Столбец {chr(65 + col)}: есть нечисловые значения")

            if len(number_cols) == 1:
                structure['sum_col'] = number_cols[0]
                print(f"Выбран столбец {chr(65 + structure['sum_col'])} как единственный с числами")
            elif len(number_cols) > 1:
                max_val = 0
                for col in number_cols:
                    for val in col_stats[col]['values']:
                        try:
                            num = float(val.replace(',', '.'))
                            if num > max_val:
                                max_val = num
                                structure['sum_col'] = col
                        except:
                            pass
                print(f"Выбран столбец {chr(65 + structure['sum_col'])} с наибольшими значениями")
            else:
                structure['sum_col'] = 5
                print(f"Не найдено чисел, используем F")

        print(f"\n=== Итоговая структура ===")
        print(f"Столбец с суммой: {chr(65 + structure['sum_col'])} (индекс {structure['sum_col']})")
        print(f"Объединены F и G: {structure['has_merged_f_g']}")
        print(f"Объединены G и H: {structure['has_merged_g_h']}")
        print("===========================\n")

        return structure

    async def parse_excel_file(e):
        try:
            content = await e.file.read()
            filename = e.file.name
            ui.state.excel_parser_current_filename = filename
            buffer = BytesIO(content)

            if filename.endswith('.xls'):
                df = pd.read_excel(buffer, header=None, engine='xlrd')
            else:
                df = pd.read_excel(buffer, header=None, engine='openpyxl')

            print(f"\n=== Обработка файла: {filename} ===")
            print(f"Размер: {df.shape}")

            structure = detect_file_structure(df)

            last_data_row = len(df)
            for i in range(9, len(df)):
                if i < len(df) and 0 < len(df.columns):
                    cell_val = str(df.iloc[i, 0]) if pd.notna(df.iloc[i, 0]) else ""
                    if "ИТОГО" in cell_val.upper() or "TOTAL" in cell_val.upper():
                        last_data_row = i
                        print(f"Найдена итоговая строка на позиции {i + 1}")
                        break

            current_file_data = []
            row_count = 0
            last_payer = ""

            for idx in range(9, last_data_row):
                date_val = df.iloc[idx, 0] if 0 < len(df.columns) else None
                if pd.isna(date_val) or not str(date_val).strip():
                    continue

                date_value = str(date_val).strip()

                payer_val = None
                for col in [3, 4]:
                    if col < len(df.columns):
                        val = df.iloc[idx, col]
                        if pd.notna(val) and str(val).strip():
                            payer_val = val
                            break

                if payer_val is not None:
                    last_payer = str(payer_val).strip()
                    buyer_value = last_payer
                else:
                    buyer_value = last_payer

                sum_val = None

                if structure['sum_col'] is not None and structure['sum_col'] < len(df.columns):
                    val = df.iloc[idx, structure['sum_col']]
                    if pd.notna(val):
                        val_str = str(val).strip()
                        try:
                            test_val = val_str.replace(',', '.').replace(' ', '')
                            float(test_val)
                            sum_val = val_str
                        except:
                            pass

                if sum_val is not None and buyer_value:
                    record = {
                        'date': date_value,
                        'buyer': buyer_value,
                        'pay_sum': sum_val,
                        'source_file': filename
                    }
                    current_file_data.append(record)
                    row_count += 1
                    print(f"Строка {idx + 1}: {date_value} | {buyer_value[:25]} | {sum_val}")

            ui.state.excel_parser_all_data.extend(current_file_data)

            ui.notify(
                f'✅ Файл "{filename}" обработан! Добавлено {row_count} записей. Всего: {len(ui.state.excel_parser_all_data)}',
                type='positive')
            update_table()
            update_file_info()

            print(f"\nДобавлено {row_count} записей из {filename}")
            print(f"Всего записей в накопителе: {len(ui.state.excel_parser_all_data)}")
            print("===========================\n")

            await asyncio.sleep(0.1)

        except Exception as ex:
            error_msg = f'Ошибка при обработке: {str(ex)}'
            ui.notify(error_msg, type='negative')
            print(error_msg)
            import traceback
            traceback.print_exc()

    def clear_all_data():
        """Очищает все накопленные данные"""
        ui.state.excel_parser_all_data.clear()
        update_table()
        update_file_info()
        ui.notify('🗑️ Все данные очищены', type='warning')

    def update_table():
        """Обновляет таблицу с учетом всех накопленных данных"""
        table_container.clear()
        with table_container:
            if ui.state.excel_parser_all_data:
                ui.table(
                    columns=[
                        {'name': 'date', 'label': '📅 Дата', 'field': 'date', 'sortable': True},
                        {'name': 'buyer', 'label': '👤 Плательщик', 'field': 'buyer', 'sortable': True},
                        {'name': 'pay_sum', 'label': '💰 Сумма', 'field': 'pay_sum', 'sortable': True},
                        {'name': 'source_file', 'label': '📁 Источник', 'field': 'source_file', 'sortable': True},
                    ],
                    rows=ui.state.excel_parser_all_data,
                    pagination={'rowsPerPage': 15},
                    row_key='index'
                ).classes('w-full')

                with ui.row().classes('mt-2 items-center'):
                    ui.label(f'📊 Всего записей: {len(ui.state.excel_parser_all_data)}').classes('text-caption')
                    ui.button('📥 Экспорт всех данных в CSV', on_click=export_all_to_csv).classes('ml-2')
            else:
                ui.label('📭 Нет данных для отображения. Загрузите Excel файлы.').classes('text-grey text-h6')

    def update_file_info():
        """Обновляет информацию о загруженных файлах"""
        if ui.state.excel_parser_all_data:
            unique_files = set(record.get('source_file', 'Unknown') for record in ui.state.excel_parser_all_data)
            files_count = len(unique_files)
            file_info_label.set_text(
                f'📁 Загружено файлов: {files_count} | Всего записей: {len(ui.state.excel_parser_all_data)}')
        else:
            file_info_label.set_text('📁 Нет загруженных файлов')

    def export_all_to_csv():
        """Экспортирует все данные в CSV"""
        if not ui.state.excel_parser_all_data:
            ui.notify('Нет данных для экспорта', type='warning')
            return

        df = pd.DataFrame(ui.state.excel_parser_all_data)
        csv_data = df.to_csv(index=False, encoding='utf-8-sig')

        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"export_all_{timestamp}.csv"

        ui.run_javascript(f'''
            const data = new Blob([`{csv_data}`], {{ type: 'text/csv;charset=utf-8;' }});
            const url = URL.createObjectURL(data);
            const link = document.createElement('a');
            link.href = url;
            link.setAttribute('download', '{filename}');
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(url);
        ''')

        ui.notify(f'✅ Экспортировано {len(ui.state.excel_parser_all_data)} записей', type='positive')

    def show_preview():
        """Показывает предпросмотр данных"""
        if not ui.state.excel_parser_all_data:
            ui.notify('Нет данных для предпросмотра', type='warning')
            return

        with ui.dialog() as dialog, ui.card():
            ui.label('📊 Предпросмотр данных').classes('text-h6 mb-2')
            ui.label(f'Всего записей: {len(ui.state.excel_parser_all_data)}').classes('text-caption mb-4')

            if len(ui.state.excel_parser_all_data) > 0:
                preview_data = ui.state.excel_parser_all_data[:10]
                ui.table(
                    columns=[
                        {'name': 'date', 'label': 'Дата', 'field': 'date'},
                        {'name': 'buyer', 'label': 'Плательщик', 'field': 'buyer'},
                        {'name': 'pay_sum', 'label': 'Сумма', 'field': 'pay_sum'},
                    ],
                    rows=preview_data,
                    pagination={'rowsPerPage': 10}
                ).classes('w-full')

                if len(ui.state.excel_parser_all_data) > 10:
                    ui.label(f'... и еще {len(ui.state.excel_parser_all_data) - 10} записей').classes(
                        'text-caption text-grey mt-2')

            with ui.row().classes('mt-4'):
                ui.button('Закрыть', on_click=dialog.close)
                ui.button('📥 Экспортировать все', on_click=lambda: (dialog.close(), export_all_to_csv()))

        dialog.open()

    # Создаем интерфейс
    ui.label('📊 Парсер Excel карточек 1С (Накопительный режим)').classes('text-h4 text-bold mb-2')
    ui.label('Загружайте несколько файлов - данные будут накапливаться').classes('text-subtitle1 text-grey mb-4')

    with ui.row().classes('items-center mb-4'):
        file_info_label = ui.label('📁 Нет загруженных файлов').classes('text-caption text-grey')
        ui.button('🗑️ Очистить все данные', on_click=clear_all_data, color='red').classes('ml-4')
        ui.button('👁️ Предпросмотр данных', on_click=show_preview).classes('ml-2')

    ui.upload(
        label='📁 Выберите Excel файл (.xls или .xlsx)',
        auto_upload=True,
        on_upload=parse_excel_file,
        multiple=False
    ).classes('mb-4')

    table_container = ui.column()