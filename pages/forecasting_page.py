# pages/forecasting_page.py
from nicegui import ui
import psycopg2
import pandas as pd
import numpy as np
from datetime import datetime
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from prophet import Prophet
import asyncio
import warnings
import os
from dotenv import load_dotenv

load_dotenv()
warnings.filterwarnings('ignore')


DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "database": os.getenv("DB_NAME", "postgres"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
    "port": os.getenv("DB_PORT", "5432")
}


class ForecastApp:
    def __init__(self):
        self.df_years = pd.DataFrame()
        self.df_sales = pd.DataFrame()
        self.df_directions = pd.DataFrame()
        self.df_oai_groups = pd.DataFrame()
        self.df_kn_groups = pd.DataFrame()
        self.df_reklama_groups = pd.DataFrame()
        self.df_tk_groups = pd.DataFrame()

        self.monthly_df = None
        self.weekly_df = None
        self.daily_df = None

        # Контейнеры
        self.years_container = None
        self.forecast_container = None
        self.direction_container = None
        self.oai_group_container = None
        self.tk_group_container = None
        self.reklama_group_container = None
        self.kn_group_container = None
        self.progress_container = None

        self.progress_bar = None
        self.progress_text = None
        self.select_year = None
        self.agg_select = None

    def get_db_connection(self):
        try:
            return psycopg2.connect(**DB_CONFIG)
        except Exception as e:
            ui.notify(f'Ошибка подключения к БД: {e}', type='negative')
            return None

    # ====================== ЗАГРУЗКА ДАННЫХ ======================
    def load_years_data(self):
        conn = self.get_db_connection()
        if not conn: return pd.DataFrame()
        try:
            query = """
                SELECT year, SUM(total_amount_plan) as plan, SUM(total_amount_summary) as fact
                FROM kamtent.division_results
                GROUP BY year ORDER BY year
            """
            return pd.read_sql(query, conn)
        finally:
            conn.close()

    def load_directions_data(self):
        conn = self.get_db_connection()
        if not conn: return pd.DataFrame()
        try:
            query = """
                SELECT year, direction, total_amount_actual as actual, total_amount_plan as plan
                FROM kamtent.yearly_division_results
                ORDER BY year, direction
            """
            return pd.read_sql(query, conn)
        finally:
            conn.close()

    def load_oai_group_data(self):
        conn = self.get_db_connection()
        if not conn: return pd.DataFrame()
        try:
            query = """
                SELECT year, month, direction, group_product, pay_summ
                FROM kamtent.monthly_group_product WHERE direction = 'ОАИ'
                ORDER BY year, month, group_product
            """
            return pd.read_sql(query, conn)
        finally:
            conn.close()

    def load_kn_groups_data(self):
        conn = self.get_db_connection()
        if not conn: return pd.DataFrame()
        try:
            query = """
                SELECT year, month, direction, group_product, pay_summ
                FROM kamtent.monthly_group_product WHERE direction = 'КН'
                ORDER BY year, month, group_product
            """
            return pd.read_sql(query, conn)
        finally:
            conn.close()

    def load_reklama_groups_data(self):
        conn = self.get_db_connection()
        if not conn: return pd.DataFrame()
        try:
            query = """
                SELECT year, month, direction, group_product, pay_summ
                FROM kamtent.monthly_group_product WHERE direction = 'РЕКЛАМА'
                ORDER BY year, month, group_product
            """
            return pd.read_sql(query, conn)
        finally:
            conn.close()

    def load_tk_groups_data(self):
        conn = self.get_db_connection()
        if not conn: return pd.DataFrame()
        try:
            query = """
                SELECT year, month, direction, group_product, pay_summ
                FROM kamtent.monthly_group_product WHERE direction = 'ТК'
                ORDER BY year, month, group_product
            """
            return pd.read_sql(query, conn)
        finally:
            conn.close()

    def load_sales_data(self):
        conn = self.get_db_connection()
        if not conn: return pd.DataFrame()
        try:
            query = """
                SELECT pay_date, pay_summ as sales_sum
                FROM kamtent.sales WHERE pay_date IS NOT NULL
                ORDER BY pay_date
            """
            df = pd.read_sql(query, conn)
            df['pay_date'] = pd.to_datetime(df['pay_date'])
            return df
        finally:
            conn.close()

    # ====================== ВЫБОР ИСТОРИЧЕСКИХ ГОДОВ ======================
    def _get_historical_years(self, target_year, df):
        current_year = datetime.now().year
        if target_year > current_year:
            years = sorted(df['year'].unique())
        else:
            years = sorted([y for y in df['year'].unique() if y < target_year])
            if not years:
                years = sorted(df['year'].unique())[-4:]
        return years

    # ====================== КОЭФФИЦИЕНТЫ С НОРМАЛИЗАЦИЕЙ ======================
    def calculate_direction_coefficients(self, target_year):
        if self.df_directions.empty:
            self.df_directions = self.load_directions_data()
        if self.df_directions.empty:
            return {}

        years = self._get_historical_years(target_year, self.df_directions)

        print(f"\n{'='*75}")
        print(f"КОЭФФИЦИЕНТЫ ПО НАПРАВЛЕНИЯМ для {target_year} | Годы: {years}")
        print(f"{'='*75}")

        all_coefs = {}
        for year in years:
            df_year = self.df_directions[self.df_directions['year'] == year]
            totals = df_year.groupby('direction')['actual'].sum()
            total = totals.sum()
            if total > 0:
                for direction, val in totals.items():
                    if direction not in all_coefs:
                        all_coefs[direction] = []
                    all_coefs[direction].append(val / total)

        coefficients = {d: sum(v)/len(v) for d, v in all_coefs.items()}

        total = sum(coefficients.values())
        if abs(total - 1.0) > 0.0001 and total > 0:
            coefficients = {d: v / total for d, v in coefficients.items()}

        return coefficients

    def calculate_oai_group_coefficients(self, target_year):
        if self.df_oai_groups.empty:
            self.df_oai_groups = self.load_oai_group_data()
        if self.df_oai_groups.empty:
            return {}

        years = self._get_historical_years(target_year, self.df_oai_groups)
        all_coefs = {}
        for year in years:
            df_year = self.df_oai_groups[self.df_oai_groups['year'] == year]
            totals = df_year.groupby('group_product')['pay_summ'].sum()
            total = totals.sum()
            if total > 0:
                for g, v in totals.items():
                    if g not in all_coefs: all_coefs[g] = []
                    all_coefs[g].append(v / total)

        coefs = {g: sum(v)/len(v) for g, v in all_coefs.items()}
        total = sum(coefs.values())
        if abs(total - 1.0) > 0.0001 and total > 0:
            coefs = {g: v / total for g, v in coefs.items()}
        return coefs

    def calculate_kn_group_coefficients(self, target_year):
        if self.df_kn_groups.empty:
            self.df_kn_groups = self.load_kn_groups_data()
        if self.df_kn_groups.empty:
            return {}

        years = self._get_historical_years(target_year, self.df_kn_groups)
        all_coefs = {}
        for year in years:
            df_year = self.df_kn_groups[self.df_kn_groups['year'] == year]
            totals = df_year.groupby('group_product')['pay_summ'].sum()
            total = totals.sum()
            if total > 0:
                for g, v in totals.items():
                    if g not in all_coefs: all_coefs[g] = []
                    all_coefs[g].append(v / total)

        coefs = {g: sum(v)/len(v) for g, v in all_coefs.items()}
        total = sum(coefs.values())
        if abs(total - 1.0) > 0.0001 and total > 0:
            coefs = {g: v / total for g, v in coefs.items()}
        return coefs

    def calculate_reklama_group_coefficients(self, target_year):
        if self.df_reklama_groups.empty:
            self.df_reklama_groups = self.load_reklama_groups_data()
        if self.df_reklama_groups.empty:
            return {}

        years = self._get_historical_years(target_year, self.df_reklama_groups)
        all_coefs = {}
        for year in years:
            df_year = self.df_reklama_groups[self.df_reklama_groups['year'] == year]
            totals = df_year.groupby('group_product')['pay_summ'].sum()
            total = totals.sum()
            if total > 0:
                for g, v in totals.items():
                    if g not in all_coefs: all_coefs[g] = []
                    all_coefs[g].append(v / total)

        coefs = {g: sum(v)/len(v) for g, v in all_coefs.items()}
        total = sum(coefs.values())
        if abs(total - 1.0) > 0.0001 and total > 0:
            coefs = {g: v / total for g, v in coefs.items()}
        return coefs

    def calculate_tk_group_coefficients(self, target_year):
        if self.df_tk_groups.empty:
            self.df_tk_groups = self.load_tk_groups_data()
        if self.df_tk_groups.empty:
            return {}

        years = self._get_historical_years(target_year, self.df_tk_groups)

        group_mapping = {
            'ТОРГОВЫЕ ТК': 'Торговые ТК',
            'ПРОМЫШЛЕННЫЕ ТК': 'Промышленные ТК',
            'СПОРТИВНЫЕ И КУЛЬТ. ТК': 'Спортивные и культ. ТК',
            'СЕЛЬСКОХОЗЯЙСТВЕННЫЕ ТК': 'Сельскохозяйственные ТК',
            'ПРОЧЕЕ': 'Прочее',
            'ОРИГИНАЛЬНЫЕ ТК': 'Прочее'
        }

        df = self.df_tk_groups.copy()
        df['group_category'] = df['group_product'].map(group_mapping).fillna('Прочее')

        all_coefs = {}
        for year in years:
            df_year = df[df['year'] == year]
            totals = df_year.groupby('group_category')['pay_summ'].sum()
            total = totals.sum()
            if total > 0:
                for g, v in totals.items():
                    if g not in all_coefs: all_coefs[g] = []
                    all_coefs[g].append(v / total)

        coefs = {g: sum(v)/len(v) for g, v in all_coefs.items()}
        total = sum(coefs.values())
        if abs(total - 1.0) > 0.0001 and total > 0:
            coefs = {g: v / total for g, v in coefs.items()}
        return coefs

    # ====================== РАЗБИВКА ======================
    def split_forecast_by_directions(self, monthly_forecast, target_year):
        coefficients = self.calculate_direction_coefficients(target_year)
        if not coefficients:
            return None

        result = []
        for _, row in monthly_forecast.iterrows():
            month = row['month']
            total = row['forecast']
            values = {d: total * c for d, c in coefficients.items()}

            directions_list = list(coefficients.keys())
            rounded = {}
            remaining = total
            for d in directions_list[:-1]:
                val = max(1000, round(values[d] / 1000) * 1000)
                rounded[d] = val
                remaining -= val
            rounded[directions_list[-1]] = max(0, remaining)

            for d, v in rounded.items():
                result.append({'month': month, 'direction': d, 'forecast': v})

        return pd.DataFrame(result)

    def split_oai_by_groups(self, oai_forecast, target_year):
        coef = self.calculate_oai_group_coefficients(target_year)
        if not coef: return None
        total = oai_forecast['forecast'].sum()
        result = [{'group': g, 'forecast': max(1000, round(total * c / 1000) * 1000)} for g, c in coef.items()]
        df = pd.DataFrame(result)
        diff = total - df['forecast'].sum()
        if diff != 0 and not df.empty:
            df.loc[df['forecast'].idxmax(), 'forecast'] += diff
        return df

    def split_kn_by_groups(self, kn_forecast, target_year):
        coef = self.calculate_kn_group_coefficients(target_year)
        if not coef: return None
        total = kn_forecast['forecast'].sum()
        result = [{'group': g, 'forecast': max(1000, round(total * c / 1000) * 1000)} for g, c in coef.items()]
        df = pd.DataFrame(result)
        diff = total - df['forecast'].sum()
        if diff != 0 and not df.empty:
            df.loc[df['forecast'].idxmax(), 'forecast'] += diff
        return df

    def split_reklama_by_groups(self, reklama_forecast, target_year):
        coef = self.calculate_reklama_group_coefficients(target_year)
        if not coef: return None
        total = reklama_forecast['forecast'].sum()
        result = [{'group': g, 'forecast': max(1000, round(total * c / 1000) * 1000)} for g, c in coef.items()]
        df = pd.DataFrame(result)
        diff = total - df['forecast'].sum()
        if diff != 0 and not df.empty:
            df.loc[df['forecast'].idxmax(), 'forecast'] += diff
        return df

    def split_tk_by_groups(self, tk_forecast, target_year):
        coef = self.calculate_tk_group_coefficients(target_year)
        if not coef: return None
        total = tk_forecast['forecast'].sum()
        result = []
        for g, c in coef.items():
            value = 50000 if g == 'Строители (пологи/шторы)ТК' else max(1000, round(total * c / 1000) * 1000)
            result.append({'group': g, 'forecast': value})
        df = pd.DataFrame(result)
        diff = total - df['forecast'].sum()
        if diff != 0 and not df.empty:
            valid = df[df['group'] != 'Строители (пологи/шторы)ТК']
            if not valid.empty:
                df.loc[valid['forecast'].idxmax(), 'forecast'] += diff
        return df

    # ====================== ПОДГОТОВКА ДАННЫХ ======================
    def prepare_monthly_data(self, df):
        df = df.copy()
        df['year'] = df['pay_date'].dt.year
        df['month'] = df['pay_date'].dt.month
        monthly = df.groupby(['year', 'month'])['sales_sum'].sum().reset_index()
        monthly['Date'] = pd.to_datetime(monthly[['year', 'month']].assign(day=1))
        monthly = monthly[['Date', 'sales_sum']].rename(columns={'sales_sum': 'Sales'}).set_index('Date').sort_index()
        monthly['Sales'] = monthly['Sales'].interpolate('linear').bfill().ffill()
        return monthly

    def prepare_weekly_data(self, df):
        df = df.copy().set_index('pay_date').sort_index()
        weekly = df.resample('W-MON')['sales_sum'].sum().to_frame('Sales')
        weekly['Sales'] = weekly['Sales'].interpolate('linear').bfill().ffill()
        return weekly

    def prepare_daily_data(self, df):
        df = df.copy().set_index('pay_date').sort_index()
        daily = df.resample('D')['sales_sum'].sum().to_frame('Sales')
        daily['Sales'] = daily['Sales'].interpolate('time').bfill().ffill()
        return daily

    def round_amount(self, amount, precision='hundreds_thousands'):
        """Округление суммы до нужной точности"""
        if precision == 'hundreds_thousands':
            return round(amount / 100000) * 100000
        elif precision == 'tens_thousands':
            return round(amount / 10000) * 10000
        elif precision == 'thousands':
            return round(amount / 1000) * 1000
        else:
            return round(amount, 2)

    async def update_progress(self, value, text):
        if self.progress_bar:
            self.progress_bar.set_value(value)
        if self.progress_text:
            self.progress_text.set_text(text)
        await asyncio.sleep(0.05)

    # ====================== ПРОГНОЗ ======================
    # Здесь оставляем твои оригинальные методы прогноза без изменений
    def forecast_for_year(self, data, agg_level, target_year, min_monthly=2100000):
        if agg_level == 'monthly':
            return self.forecast_monthly(data, target_year, min_monthly)
        elif agg_level == 'weekly':
            return self.forecast_weekly_optimized(data, target_year, min_monthly)
        elif agg_level == 'daily':
            return self.forecast_daily(data, target_year, min_monthly)
        return None

    def forecast_monthly(self, df, target_year, min_monthly=2100000):
        """Прогноз на основе месячных данных"""
        train = df[df.index <= f'{target_year - 1}-12-31']

        if len(train) < 12:
            return self.fallback_forecast(train, target_year, min_monthly, 'monthly')

        models = {}

        # Holt-Winters
        try:
            train_values = train['Sales'].bfill().ffill()
            if len(train_values) >= 12:
                hw_model = ExponentialSmoothing(
                    train_values,
                    trend='add',
                    seasonal='add',
                    seasonal_periods=12,
                    damped_trend=True
                )
                hw_fit = hw_model.fit()
                hw_forecast = hw_fit.forecast(12)
                models['Holt-Winters'] = hw_forecast
        except Exception as e:
            print(f"Holt-Winters failed: {e}")

        # Prophet
        try:
            prophet_df = train.reset_index()[['Date', 'Sales']].rename(columns={'Date': 'ds', 'Sales': 'y'})
            prophet_df['y'] = prophet_df['y'].bfill().ffill()
            if len(prophet_df) >= 12:
                prophet_model = Prophet(
                    seasonality_mode='multiplicative',
                    yearly_seasonality=True,
                    seasonality_prior_scale=0.1
                )
                prophet_model.fit(prophet_df)
                future = prophet_model.make_future_dataframe(periods=12, freq='ME')
                prophet_forecast = prophet_model.predict(future)['yhat'][-12:].values
                models['Prophet'] = prophet_forecast
        except Exception as e:
            print(f"Prophet failed: {e}")

        if models:
            if len(models) > 1:
                ensemble = pd.DataFrame(models).mean(axis=1)
                forecast = ensemble.values
                model_name = 'Ensemble'
            else:
                model_name = list(models.keys())[0]
                forecast = models[model_name]
        else:
            return self.fallback_forecast(train, target_year, min_monthly, 'monthly')

        forecast = [max(float(x), min_monthly) for x in forecast]

        forecast_dates = pd.date_range(start=f'{target_year}-01-31', periods=12, freq='ME')

        forecast_df = pd.DataFrame({
            'month': forecast_dates,
            'forecast': forecast
        })

        # Исправленный вызов round_amount
        forecast_df['forecast'] = forecast_df['forecast'].apply(
            lambda x: self.round_amount(x, 'hundreds_thousands')
        )

        stats = {
            'total_forecast': self.round_amount(sum(forecast_df['forecast']), 'hundreds_thousands'),
            'avg_monthly': self.round_amount(np.mean(forecast_df['forecast']), 'hundreds_thousands'),
            'min_month': self.round_amount(min(forecast_df['forecast']), 'hundreds_thousands'),
            'max_month': self.round_amount(max(forecast_df['forecast']), 'hundreds_thousands'),
            'model_used': model_name
        }

        return forecast_df, stats

    def forecast_weekly_optimized(self, df, target_year, min_monthly):
        """Оптимизированный прогноз на основе недельных данных"""
        train = df[df.index <= f'{target_year - 1}-12-31']

        if len(train) < 26:
            return self.fallback_forecast(train, target_year, min_monthly, 'weekly')

        models = {}

        # Holt-Winters
        try:
            train_values = train['Sales'].bfill().ffill()
            hw_model = ExponentialSmoothing(
                train_values,
                trend='add',
                seasonal='add',
                seasonal_periods=13,
                damped_trend=True
            )
            hw_fit = hw_model.fit()
            hw_forecast = hw_fit.forecast(26)
            models['Holt-Winters'] = hw_forecast
        except Exception as e:
            print(f"Holt-Winters failed: {e}")

        # Prophet
        try:
            prophet_df = train.reset_index().rename(columns={'pay_date': 'ds', 'Sales': 'y'})
            prophet_df['y'] = prophet_df['y'].bfill().ffill()
            prophet_model = Prophet(
                seasonality_mode='multiplicative',
                yearly_seasonality=True,
                weekly_seasonality=False,
                seasonality_prior_scale=0.1
            )
            prophet_model.fit(prophet_df)
            future = prophet_model.make_future_dataframe(periods=26, freq='W-MON')
            prophet_forecast = prophet_model.predict(future)['yhat'][-26:].values
            models['Prophet'] = prophet_forecast
        except Exception as e:
            print(f"Prophet failed: {e}")

        if models:
            if len(models) > 1:
                ensemble = pd.DataFrame(models).mean(axis=1)
                weekly_forecast = ensemble
                model_name = 'Ensemble'
            else:
                model_name = list(models.keys())[0]
                weekly_forecast = models[model_name]
        else:
            return self.fallback_forecast(train, target_year, min_monthly, 'weekly')

        # Проверяем длину прогноза
        print(f"Длина прогноза: {len(weekly_forecast)}")

        # Если прогноз короче 52 недель, интерполируем
        if len(weekly_forecast) < 52:
            x_old = np.linspace(0, 1, len(weekly_forecast))
            x_new = np.linspace(0, 1, 52)
            try:
                weekly_forecast_full = np.interp(x_new, x_old, weekly_forecast)
            except Exception as e:
                print(f"Ошибка интерполяции: {e}")
                # Если интерполяция не работает, просто повторяем последнее значение
                weekly_forecast_full = np.full(52, weekly_forecast[-1])
        elif len(weekly_forecast) > 52:
            weekly_forecast_full = weekly_forecast[:52]
        else:
            weekly_forecast_full = weekly_forecast

        # Убеждаемся, что длина равна 52
        if len(weekly_forecast_full) != 52:
            print(f"Некорректная длина прогноза: {len(weekly_forecast_full)}")
            # Заполняем до 52 недель
            if len(weekly_forecast_full) < 52:
                weekly_forecast_full = np.append(weekly_forecast_full,
                                                 [weekly_forecast_full[-1]] * (52 - len(weekly_forecast_full)))
            else:
                weekly_forecast_full = weekly_forecast_full[:52]

        # Создаем даты для прогноза
        forecast_dates = pd.date_range(start=f'{target_year}-01-05', periods=52, freq='W-MON')

        # Создаем DataFrame с прогнозом
        forecast_df = pd.DataFrame({'Date': forecast_dates, 'Forecast': weekly_forecast_full})
        forecast_df.set_index('Date', inplace=True)

        # Агрегируем по месяцам
        monthly_forecast = forecast_df.resample('ME')['Forecast'].sum()

        # Применяем минимальный порог
        monthly_forecast = [max(x, min_monthly) for x in monthly_forecast]

        # Убеждаемся, что у нас 12 месяцев
        if len(monthly_forecast) < 12:
            monthly_forecast = monthly_forecast + [monthly_forecast[-1]] * (12 - len(monthly_forecast))
        elif len(monthly_forecast) > 12:
            monthly_forecast = monthly_forecast[:12]

        forecast_dates_monthly = pd.date_range(start=f'{target_year}-01-31', periods=12, freq='ME')

        result_df = pd.DataFrame({
            'month': forecast_dates_monthly,
            'forecast': monthly_forecast
        })

        result_df['forecast'] = result_df['forecast'].apply(
            lambda x: self.round_amount(x, 'hundreds_thousands')
        )

        stats = {
            'total_forecast': self.round_amount(sum(result_df['forecast']), 'hundreds_thousands'),
            'avg_monthly': self.round_amount(np.mean(result_df['forecast']), 'hundreds_thousands'),
            'min_month': self.round_amount(min(result_df['forecast']), 'hundreds_thousands'),
            'max_month': self.round_amount(max(result_df['forecast']), 'hundreds_thousands'),
            'model_used': f"{model_name} (weekly-based)"
        }

        return result_df, stats

    def forecast_daily(self, df, target_year, min_monthly):
        """Прогноз на основе ежедневных данных с агрегацией в месячные"""
        days_in_year = 366 if (target_year % 4 == 0 and (target_year % 100 != 0 or target_year % 400 == 0)) else 365
        train = df[df.index <= f'{target_year - 1}-12-31']

        if len(train) < 180:
            return self.fallback_forecast(train, target_year, min_monthly, 'daily')

        try:
            prophet_df = train.reset_index()[['pay_date', 'Sales']].rename(
                columns={'pay_date': 'ds', 'Sales': 'y'}
            )
            prophet_df['y'] = prophet_df['y'].bfill().ffill()
            prophet_model = Prophet(
                seasonality_mode='multiplicative',
                yearly_seasonality=True,
                weekly_seasonality=True,
                seasonality_prior_scale=0.1
            )
            prophet_model.fit(prophet_df)
            future = prophet_model.make_future_dataframe(periods=min(days_in_year, 90), freq='D')
            daily_forecast = prophet_model.predict(future)['yhat'][-min(days_in_year, 90):].values

            if len(daily_forecast) < days_in_year:
                x_old = np.linspace(0, 1, len(daily_forecast))
                x_new = np.linspace(0, 1, days_in_year)
                daily_forecast = np.interp(x_new, x_old, daily_forecast)
        except Exception as e:
            print(f"Prophet failed: {e}")
            return self.fallback_forecast(train, target_year, min_monthly, 'daily')

        forecast_dates = pd.date_range(start=f'{target_year}-01-01', periods=days_in_year, freq='D')
        forecast_df = pd.DataFrame({'Date': forecast_dates, 'Forecast': daily_forecast[:days_in_year]})
        forecast_df.set_index('Date', inplace=True)
        monthly_forecast = forecast_df.resample('ME')['Forecast'].sum()
        monthly_forecast = [max(x, min_monthly) for x in monthly_forecast]

        forecast_dates_monthly = pd.date_range(start=f'{target_year}-01-31', periods=12, freq='ME')

        result_df = pd.DataFrame({
            'month': forecast_dates_monthly,
            'forecast': monthly_forecast
        })

        result_df['forecast'] = result_df['forecast'].apply(
            lambda x: self.round_amount(x, 'hundreds_thousands')
        )

        stats = {
            'total_forecast': self.round_amount(sum(result_df['forecast']), 'hundreds_thousands'),
            'avg_monthly': self.round_amount(np.mean(result_df['forecast']), 'hundreds_thousands'),
            'min_month': self.round_amount(min(result_df['forecast']), 'hundreds_thousands'),
            'max_month': self.round_amount(max(result_df['forecast']), 'hundreds_thousands'),
            'model_used': "Prophet (daily-based)"
        }

        return result_df, stats

    def fallback_forecast(self, train, target_year, min_monthly, agg_level):
        """Запасной метод прогнозирования"""
        if len(train) > 0:
            last_values = train.tail(12)['Sales']
            avg_monthly = last_values.mean() if len(last_values) > 0 else min_monthly * 1.5
        else:
            avg_monthly = min_monthly * 1.5

        monthly_forecast = [max(avg_monthly, min_monthly)] * 12
        forecast_dates = pd.date_range(start=f'{target_year}-01-31', periods=12, freq='ME')

        result_df = pd.DataFrame({
            'month': forecast_dates,
            'forecast': monthly_forecast
        })

        result_df['forecast'] = result_df['forecast'].apply(
            lambda x: self.round_amount(x, 'hundreds_thousands')
        )

        stats = {
            'total_forecast': self.round_amount(sum(result_df['forecast']), 'hundreds_thousands'),
            'avg_monthly': self.round_amount(np.mean(result_df['forecast']), 'hundreds_thousands'),
            'min_month': self.round_amount(min(result_df['forecast']), 'hundreds_thousands'),
            'max_month': self.round_amount(max(result_df['forecast']), 'hundreds_thousands'),
            'model_used': f"Fallback ({agg_level})"
        }

        return result_df, stats
    def update_years_table(self):
        """Обновление таблицы с данными по годам"""
        self.df_years = self.load_years_data()

        self.years_container.clear()

        if self.df_years.empty:
            with self.years_container:
                ui.label('Нет данных по годам').classes('text-bold text-red')
            return

        with self.years_container:
            ui.label('Данные по годам:').classes('text-h6 text-bold mb-2')

            columns = [
                {'name': 'year', 'label': 'Год', 'field': 'year', 'align': 'center'},
                {'name': 'plan', 'label': 'План (₽)', 'field': 'plan', 'align': 'right'},
                {'name': 'fact', 'label': 'Факт (₽)', 'field': 'fact', 'align': 'right'},
            ]

            rows = self.df_years.to_dict('records')

            table = ui.table(
                columns=columns,
                rows=rows,
                row_key='year',
                pagination={'rowsPerPage': 10}
            ).classes('w-full')

            table.add_slot('body-cell-year', '''
                <q-td key="year" :props="props" style="width: 80px; min-width: 80px; text-align: center;">
                    <div class="text-bold">{{ props.value }}</div>
                </q-td>
            ''')

            table.add_slot('body-cell-plan', '''
                <q-td key="plan" :props="props" style="text-align: right;">
                    <div class="font-mono">
                        {{ new Intl.NumberFormat('ru-RU', {minimumFractionDigits: 2, maximumFractionDigits: 2}).format(props.value) }}
                    </div>
                </q-td>
            ''')

            table.add_slot('body-cell-fact', '''
                <q-td key="fact" :props="props" style="text-align: right;">
                    <div class="font-mono">
                        {{ new Intl.NumberFormat('ru-RU', {minimumFractionDigits: 2, maximumFractionDigits: 2}).format(props.value) }}
                    </div>
                </q-td>
            ''')

            self.years_table = table

    # ====================== UI ======================
    async def on_forecast_click(self):
        selected_year = self.select_year.value
        agg_level_str = self.agg_select.value

        agg_map = {'По месяцам': 'monthly', 'По неделям': 'weekly', 'По дням': 'daily'}
        agg_level = agg_map.get(agg_level_str, 'monthly')

        self.progress_container.clear()
        with self.progress_container:
            with ui.row().classes('items-center gap-4'):
                self.progress_bar = ui.circular_progress(value=0, min=0, max=100, size='50px')
                self.progress_text = ui.label(f'Расчёт прогноза на {selected_year}...').classes('text-bold')

        await self.update_progress(10, 'Загрузка данных...')

        if self.df_sales.empty:
            self.df_sales = self.load_sales_data()
            if not self.df_sales.empty:
                self.monthly_df = self.prepare_monthly_data(self.df_sales)
                self.weekly_df = self.prepare_weekly_data(self.df_sales)
                self.daily_df = self.prepare_daily_data(self.df_sales)

        await self.update_progress(30, 'Расчёт прогноза...')

        data = {'monthly': self.monthly_df, 'weekly': self.weekly_df, 'daily': self.daily_df}.get(agg_level)

        forecast_result = self.forecast_for_year(data, agg_level, selected_year)

        await self.update_progress(70, 'Разбивка по направлениям и группам...')

        self.forecast_container.clear()
        self.direction_container.clear()

        if forecast_result is None:
            with self.forecast_container:
                ui.label('Ошибка при расчете прогноза').classes('text-bold text-red')
        else:
            forecast_df, stats = forecast_result

            # Разбиваем прогноз по направлениям
            direction_forecast = self.split_forecast_by_directions(forecast_df, selected_year)

            # Отображаем общий прогноз
            with self.forecast_container:
                ui.label(f'ПРОГНОЗ НА {selected_year} ГОД').classes('text-h5 text-bold text-blue mb-4')
                ui.label(f"Модель: {stats['model_used']}").classes('text-italic mb-2')

                # Карточки со статистикой
                with ui.row().classes('w-full gap-4 mb-6'):
                    with ui.card().classes('bg-blue-1 p-4'):
                        ui.label('Общая сумма:').classes('text-bold')
                        ui.label(f"{stats['total_forecast']:,.0f} ₽".replace(',', ' ')).classes(
                            'text-h6 text-bold text-blue')

                    with ui.card().classes('bg-green-1 p-4'):
                        ui.label('Среднемесячно:').classes('text-bold')
                        ui.label(f"{stats['avg_monthly']:,.0f} ₽".replace(',', ' ')).classes(
                            'text-h6 text-bold text-green')

                    with ui.card().classes('bg-purple-1 p-4'):
                        ui.label('Мин/Макс месяц:').classes('text-bold')
                        ui.label(f"{stats['min_month']:,.0f} / {stats['max_month']:,.0f} ₽".replace(',', ' ')).classes(
                            'text-h6 text-bold text-purple')

                # Таблица помесячного прогноза
                ui.label('Помесячный прогноз:').classes('text-h6 text-bold mb-2')

                columns = [
                    {'name': 'month', 'label': 'Месяц', 'field': 'month', 'align': 'left'},
                    {'name': 'forecast', 'label': 'Прогноз (₽)', 'field': 'forecast', 'align': 'right'},
                ]

                months_ru = {
                    1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель',
                    5: 'Май', 6: 'Июнь', 7: 'Июль', 8: 'Август',
                    9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь'
                }

                rows = []
                for _, row in forecast_df.iterrows():
                    month_num = row['month'].month
                    rows.append({
                        'month': months_ru[month_num],
                        'forecast': row['forecast']
                    })

                forecast_table = ui.table(
                    columns=columns,
                    rows=rows,
                    pagination={'rowsPerPage': 15}
                ).classes('w-full')

                forecast_table.add_slot('body-cell-month', '''
                    <q-td key="month" :props="props" style="text-align: left;">
                        <div class="text-bold">
                            {{ props.value }}
                        </div>
                    </q-td>
                ''')

                forecast_table.add_slot('body-cell-forecast', '''
                    <q-td key="forecast" :props="props" style="text-align: right;">
                        <div class="font-mono text-bold">
                            {{ new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 0}).format(props.value) }}
                        </div>
                    </q-td>
                ''')

            # Отображаем прогноз по направлениям
            if direction_forecast is not None and not direction_forecast.empty:
                with self.direction_container:
                    ui.label('ПРОГНОЗ ПО ТОВАРНЫМ НАПРАВЛЕНИЯМ:').classes('text-h5 text-bold text-green mb-4')

                    # Получаем уникальные направления
                    directions = sorted(direction_forecast['direction'].unique())

                    # Создаем колонки для таблицы
                    columns = [{'name': 'month', 'label': 'Месяц', 'field': 'month', 'align': 'left'}]
                    for direction in directions:
                        columns.append({
                            'name': direction,
                            'label': direction,
                            'field': direction,
                            'align': 'right'
                        })

                    # Формируем строки
                    months_ru = {
                        1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель',
                        5: 'Май', 6: 'Июнь', 7: 'Июль', 8: 'Август',
                        9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь'
                    }

                    rows = []
                    for month_num in range(1, 13):
                        row = {'month': months_ru[month_num]}
                        month_data = direction_forecast[direction_forecast['month'].dt.month == month_num]
                        for direction in directions:
                            value = month_data[month_data['direction'] == direction]['forecast'].sum()
                            row[direction] = value if value > 0 else 0
                        rows.append(row)

                    # Добавляем итоговую строку
                    total_row = {'month': 'ИТОГО:'}
                    for direction in directions:
                        total = direction_forecast[direction_forecast['direction'] == direction]['forecast'].sum()
                        total_row[direction] = total
                    rows.append(total_row)

                    # Создаем таблицу
                    direction_table = ui.table(
                        columns=columns,
                        rows=rows,
                        pagination={'rowsPerPage': 15}
                    ).classes('w-full')

                    # Добавляем форматирование для колонки месяца
                    direction_table.add_slot('body-cell-month', '''
                        <q-td key="month" :props="props" style="text-align: left;">
                            <div class="text-bold">
                                {{ props.value }}
                            </div>
                        </q-td>
                    ''')

                    # Добавляем форматирование для всех числовых колонок
                    for direction in directions:
                        direction_table.add_slot(f'body-cell-{direction}', f'''
                            <q-td key="{direction}" :props="props" style="text-align: right;">
                                <div class="font-mono text-bold">
                                    {{{{ new Intl.NumberFormat('ru-RU', {{maximumFractionDigits: 0}}).format(props.value) }}}}
                                </div>
                            </q-td>
                        ''')

                    direction_table.rows = rows

            # После отображения прогноза по направлениям, добавляем разбивку ОАИ
            if direction_forecast is not None and not direction_forecast.empty:
                # Получаем прогноз только для ОАИ
                oai_forecast = direction_forecast[direction_forecast['direction'] == 'ОАИ'][['month', 'forecast']]
                if not oai_forecast.empty:
                    # Разбиваем ОАИ по товарным группам
                    oai_groups_forecast = self.split_oai_by_groups(oai_forecast, selected_year)

                    if oai_groups_forecast is not None and not oai_groups_forecast.empty:
                        with self.oai_group_container:
                            ui.label('ПРОГНОЗ ПО ТОВАРНЫМ ГРУППАМ ОАИ:').classes('text-h5 text-bold text-orange mb-4')

                            # Получаем уникальные группы
                            groups = sorted(oai_groups_forecast['group'].unique())

                            # Создаем колонки для таблицы
                            columns = [
                                {'name': 'group', 'label': 'Товарная группа', 'field': 'group', 'align': 'left'},
                                {'name': 'forecast', 'label': 'Прогноз (₽)', 'field': 'forecast', 'align': 'right'},
                                {'name': 'percentage', 'label': 'Доля (%)', 'field': 'percentage', 'align': 'right'}
                            ]

                            # Формируем строки
                            total_oai = oai_groups_forecast['forecast'].sum()
                            rows = []
                            for _, row in oai_groups_forecast.iterrows():
                                percentage = (row['forecast'] / total_oai) * 100 if total_oai > 0 else 0
                                rows.append({
                                    'group': row['group'],
                                    'forecast': row['forecast'],
                                    'percentage': percentage
                                })

                            # Добавляем итоговую строку
                            rows.append({
                                'group': 'ИТОГО:',
                                'forecast': total_oai,
                                'percentage': 100.0
                            })

                            # Создаем таблицу
                            group_table = ui.table(
                                columns=columns,
                                rows=rows,
                                pagination={'rowsPerPage': 20}
                            ).classes('w-full')

                            # Добавляем форматирование
                            group_table.add_slot('body-cell-group', '''
                                <q-td key="group" :props="props" style="text-align: left;">
                                    <div class="text-bold">
                                        {{ props.value }}
                                    </div>
                                </q-td>
                            ''')

                            group_table.add_slot('body-cell-forecast', '''
                                <q-td key="forecast" :props="props" style="text-align: right;">
                                    <div class="font-mono text-bold">
                                        {{ new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 0}).format(props.value) }}
                                    </div>
                                </q-td>
                            ''')

                            group_table.add_slot('body-cell-percentage', '''
                                <q-td key="percentage" :props="props" style="text-align: right;">
                                    <div class="font-mono text-bold">
                                        {{ props.value.toFixed(1) }}%
                                    </div>
                                </q-td>
                            ''')

                            group_table.rows = rows

            # После разбивки ОАИ, добавляем разбивку КН
            if direction_forecast is not None and not direction_forecast.empty:
                # Получаем прогноз только для КН
                kn_forecast = direction_forecast[direction_forecast['direction'] == 'КН'][['month', 'forecast']]
                if not kn_forecast.empty:
                    # Разбиваем КН по товарным группам
                    kn_groups_forecast = self.split_kn_by_groups(kn_forecast, selected_year)

                    if kn_groups_forecast is not None and not kn_groups_forecast.empty:
                        with self.kn_group_container:
                            ui.label('ПРОГНОЗ ПО ТОВАРНЫМ ГРУППАМ КН:').classes('text-h5 text-bold text-purple mb-4')

                            # Получаем уникальные группы
                            kn_groups = sorted(kn_groups_forecast['group'].unique())

                            # Создаем колонки для таблицы
                            columns = [
                                {'name': 'group', 'label': 'Товарная группа', 'field': 'group', 'align': 'left'},
                                {'name': 'forecast', 'label': 'Прогноз (₽)', 'field': 'forecast', 'align': 'right'},
                                {'name': 'percentage', 'label': 'Доля (%)', 'field': 'percentage', 'align': 'right'}
                            ]

                            # Формируем строки
                            total_kn = kn_groups_forecast['forecast'].sum()
                            rows = []
                            for _, row in kn_groups_forecast.iterrows():
                                percentage = (row['forecast'] / total_kn) * 100 if total_kn > 0 else 0
                                rows.append({
                                    'group': row['group'],
                                    'forecast': row['forecast'],
                                    'percentage': percentage
                                })

                            # Добавляем итоговую строку
                            rows.append({
                                'group': 'ИТОГО:',
                                'forecast': total_kn,
                                'percentage': 100.0
                            })

                            # Создаем таблицу
                            kn_group_table = ui.table(
                                columns=columns,
                                rows=rows,
                                pagination={'rowsPerPage': 20}
                            ).classes('w-full')

                            # Добавляем форматирование
                            kn_group_table.add_slot('body-cell-group', '''
                                <q-td key="group" :props="props" style="text-align: left;">
                                    <div class="text-bold">
                                        {{ props.value }}
                                    </div>
                                </q-td>
                            ''')

                            kn_group_table.add_slot('body-cell-forecast', '''
                                <q-td key="forecast" :props="props" style="text-align: right;">
                                    <div class="font-mono text-bold">
                                        {{ new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 0}).format(props.value) }}
                                    </div>
                                </q-td>
                            ''')

                            kn_group_table.add_slot('body-cell-percentage', '''
                                <q-td key="percentage" :props="props" style="text-align: right;">
                                    <div class="font-mono text-bold">
                                        {{ props.value.toFixed(1) }}%
                                    </div>
                                </q-td>
                            ''')

                            kn_group_table.rows = rows

            # После разбивки ОАИ, добавляем разбивку РЕКЛАМА
            if direction_forecast is not None and not direction_forecast.empty:
                # Получаем прогноз только для КН
                reklama_forecast = direction_forecast[direction_forecast['direction'] == 'РЕКЛАМА'][
                    ['month', 'forecast']]
                if not reklama_forecast.empty:
                    # Разбиваем КН по товарным группам
                    reklama_groups_forecast = self.split_reklama_by_groups(reklama_forecast, selected_year)

                    if reklama_groups_forecast is not None and not reklama_groups_forecast.empty:
                        with self.reklama_group_container:
                            ui.label('ПРОГНОЗ ПО ТОВАРНЫМ ГРУППАМ РЕКЛАМА:').classes(
                                'text-h5 text-bold text-purple mb-4')

                            # Получаем уникальные группы
                            reklama_groups = sorted(reklama_groups_forecast['group'].unique())

                            # Создаем колонки для таблицы
                            columns = [
                                {'name': 'group', 'label': 'Товарная группа', 'field': 'group', 'align': 'left'},
                                {'name': 'forecast', 'label': 'Прогноз (₽)', 'field': 'forecast', 'align': 'right'},
                                {'name': 'percentage', 'label': 'Доля (%)', 'field': 'percentage', 'align': 'right'}
                            ]

                            # Формируем строки
                            total_reklama = reklama_groups_forecast['forecast'].sum()
                            rows = []
                            for _, row in reklama_groups_forecast.iterrows():
                                percentage = (row['forecast'] / total_reklama) * 100 if total_reklama > 0 else 0
                                rows.append({
                                    'group': row['group'],
                                    'forecast': row['forecast'],
                                    'percentage': percentage
                                })

                            # Добавляем итоговую строку
                            rows.append({
                                'group': 'ИТОГО:',
                                'forecast': total_reklama,
                                'percentage': 100.0
                            })

                            # Создаем таблицу
                            reklama_group_table = ui.table(
                                columns=columns,
                                rows=rows,
                                pagination={'rowsPerPage': 20}
                            ).classes('w-full')

                            # Добавляем форматирование
                            reklama_group_table.add_slot('body-cell-group', '''
                                <q-td key="group" :props="props" style="text-align: left;">
                                    <div class="text-bold">
                                        {{ props.value }}
                                    </div>
                                </q-td>
                            ''')

                            reklama_group_table.add_slot('body-cell-forecast', '''
                                <q-td key="forecast" :props="props" style="text-align: right;">
                                    <div class="font-mono text-bold">
                                        {{ new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 0}).format(props.value) }}
                                    </div>
                                </q-td>
                            ''')

                            reklama_group_table.add_slot('body-cell-percentage', '''
                                <q-td key="percentage" :props="props" style="text-align: right;">
                                    <div class="font-mono text-bold">
                                        {{ props.value.toFixed(1) }}%
                                    </div>
                                </q-td>
                            ''')

                            reklama_group_table.rows = rows

            # После разбивки КН, добавляем разбивку ТК
            if direction_forecast is not None and not direction_forecast.empty:
                # Получаем прогноз только для ТК
                tk_forecast = direction_forecast[direction_forecast['direction'] == 'ТК'][['month', 'forecast']]
                if not tk_forecast.empty:
                    # Разбиваем ТК по товарным группам
                    tk_groups_forecast = self.split_tk_by_groups(tk_forecast, selected_year)

                    if tk_groups_forecast is not None and not tk_groups_forecast.empty:
                        with self.tk_group_container:
                            ui.label('ПРОГНОЗ ПО ТОВАРНЫМ ГРУППАМ ТК:').classes('text-h5 text-bold text-yellow mb-4')

                            # Получаем уникальные группы
                            tk_groups = sorted(tk_groups_forecast['group'].unique())

                            # Создаем колонки для таблицы
                            columns = [
                                {'name': 'group', 'label': 'Товарная группа', 'field': 'group', 'align': 'left'},
                                {'name': 'forecast', 'label': 'Прогноз (₽)', 'field': 'forecast', 'align': 'right'},
                                {'name': 'percentage', 'label': 'Доля (%)', 'field': 'percentage', 'align': 'right'}
                            ]

                            # Формируем строки
                            total_tk = tk_groups_forecast['forecast'].sum()
                            rows = []
                            for _, row in tk_groups_forecast.iterrows():
                                percentage = (row['forecast'] / total_tk) * 100 if total_tk > 0 else 0
                                rows.append({
                                    'group': row['group'],
                                    'forecast': row['forecast'],
                                    'percentage': percentage
                                })

                            # Добавляем итоговую строку
                            rows.append({
                                'group': 'ИТОГО:',
                                'forecast': total_tk,
                                'percentage': 100.0
                            })

                            # Создаем таблицу
                            tk_group_table = ui.table(
                                columns=columns,
                                rows=rows,
                                pagination={'rowsPerPage': 20}
                            ).classes('w-full')

                            # Добавляем форматирование
                            tk_group_table.add_slot('body-cell-group', '''
                                <q-td key="group" :props="props" style="text-align: left;">
                                    <div class="text-bold">
                                        {{ props.value }}
                                    </div>
                                </q-td>
                            ''')

                            tk_group_table.add_slot('body-cell-forecast', '''
                                <q-td key="forecast" :props="props" style="text-align: right;">
                                    <div class="font-mono text-bold">
                                        {{ new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 0}).format(props.value) }}
                                    </div>
                                </q-td>
                            ''')

                            tk_group_table.add_slot('body-cell-percentage', '''
                                <q-td key="percentage" :props="props" style="text-align: right;">
                                    <div class="font-mono text-bold">
                                        {{ props.value.toFixed(1) }}%
                                    </div>
                                </q-td>
                            ''')

                            tk_group_table.rows = rows

        await self.update_progress(100, 'Готово!')
        await asyncio.sleep(2)
        self.progress_container.clear()

    def create_ui(self):
        ui.label('Прогнозирование поступлений средств').classes('text-h3 text-bold text-center w-full mb-6')

        with ui.row().classes('justify-center w-full gap-4 mb-6'):
            ui.button('Загрузить данные', on_click=self.on_load_all_click).classes('bg-blue-500 text-white')
            self.select_year = ui.select(list(range(2020, 2031)), label='Год прогноза', value=datetime.now().year + 1).classes('w-48')
            self.agg_select = ui.select(['По месяцам', 'По неделям', 'По дням'], label='Уровень агрегации', value='По месяцам').classes('w-48')
            ui.button('Сделать прогноз', on_click=self.on_forecast_click).classes('bg-green-500 text-white px-8')

        self.progress_container = ui.column().classes('w-full max-w-4xl mx-auto')
        self.years_container = ui.column().classes('w-full max-w-4xl mx-auto mt-6')
        self.forecast_container = ui.column().classes('w-full max-w-4xl mx-auto mt-6')
        self.direction_container = ui.column().classes('w-full max-w-4xl mx-auto mt-6')
        self.oai_group_container = ui.column().classes('w-full max-w-4xl mx-auto mt-6')
        self.tk_group_container = ui.column().classes('w-full max-w-4xl mx-auto mt-6')
        self.reklama_group_container = ui.column().classes('w-full max-w-4xl mx-auto mt-6')
        self.kn_group_container = ui.column().classes('w-full max-w-4xl mx-auto mt-6')

    def on_load_all_click(self):
        """Загрузка всех данных + отображение таблицы по годам"""
        ui.notify('Загрузка данных из базы...', type='info')

        # Загружаем данные
        self.df_years = self.load_years_data()
        self.df_sales = self.load_sales_data()
        self.df_directions = self.load_directions_data()

        # Подготавливаем данные для прогноза
        if not self.df_sales.empty:
            self.monthly_df = self.prepare_monthly_data(self.df_sales)
            self.weekly_df = self.prepare_weekly_data(self.df_sales)
            self.daily_df = self.prepare_daily_data(self.df_sales)

        # Обновляем таблицу с годами
        self.update_years_table()

        # Уведомления
        if not self.df_years.empty:
            ui.notify(f'Загружены данные по годам: {len(self.df_years)} записей', type='positive')
        if not self.df_sales.empty:
            ui.notify(f'Загружено {len(self.df_sales)} записей о продажах', type='positive')
        if not self.df_directions.empty:
            ui.notify(f'Загружены данные по направлениям', type='positive')

        # Очищаем предыдущие прогнозы
        self.forecast_container.clear()
        self.direction_container.clear()
        self.oai_group_container.clear()
        self.tk_group_container.clear()
        self.reklama_group_container.clear()
        self.kn_group_container.clear()


def create_forecasting_page():
    app = ForecastApp()
    app.create_ui()