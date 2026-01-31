import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple, List, Dict
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
import sqlite3
import pytz
import asyncio

TOKEN = API_TOKEN

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

MOSCOW_TZ = pytz.timezone('Europe/Moscow')

# --- Классы состояний FSM ---
class ChildRegistration(StatesGroup):
    waiting_for_first_name = State()
    waiting_for_last_name = State()
    waiting_for_gender = State()
    waiting_for_birth_date = State()
    waiting_for_gestation_weeks = State()
    waiting_for_gestation_days = State()
    waiting_for_birth_weight = State()
    waiting_for_birth_height = State()

class UpdateParams(StatesGroup):
    waiting_for_weight = State()
    waiting_for_height = State()

class SleepTracking(StatesGroup):
    waiting_for_sleep_type = State()

class DiaperTracking(StatesGroup):
    waiting_for_diaper_type = State()

class NoteTaking(StatesGroup):
    waiting_for_note = State()

class MedicationTracking(StatesGroup):
    waiting_for_med_name = State()
    waiting_for_weight_for_med = State()

class CustomFeedingAmount(StatesGroup):
    waiting_for_custom_amount = State()

# --- База данных ---
class Database:
    def __init__(self, db_name='baby_tracker.db'):
        self.db_name = db_name
        self.timeout = 30
        self.init_db()
    
    def get_connection(self):
        conn = sqlite3.connect(self.db_name, timeout=self.timeout)
        conn.row_factory = sqlite3.Row
        return conn
    
    def init_db(self):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            
            # Существующие таблицы
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS children (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    first_name TEXT NOT NULL,
                    last_name TEXT,
                    gender TEXT NOT NULL,
                    birth_date DATE NOT NULL,
                    gestation_weeks INTEGER NOT NULL,
                    gestation_days INTEGER NOT NULL,
                    birth_weight REAL NOT NULL,
                    birth_height INTEGER NOT NULL,
                    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS feedings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    child_id INTEGER NOT NULL,
                    start_time TIMESTAMP NOT NULL,
                    end_time TIMESTAMP,
                    prepared_ml INTEGER,
                    total_eaten_ml INTEGER,
                    is_paused INTEGER DEFAULT 0,
                    paused_at TIMESTAMP,
                    pauses_count INTEGER DEFAULT 0,
                    total_pause_duration INTEGER DEFAULT 0,
                    FOREIGN KEY (child_id) REFERENCES children (id)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS measurements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    child_id INTEGER NOT NULL,
                    weight REAL NOT NULL,
                    height INTEGER NOT NULL,
                    measurement_date DATE NOT NULL,
                    age_days INTEGER NOT NULL,
                    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (child_id) REFERENCES children (id)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    child_id INTEGER NOT NULL,
                    reminder_type TEXT NOT NULL,
                    next_reminder DATE NOT NULL,
                    frequency_days INTEGER NOT NULL,
                    is_active INTEGER DEFAULT 1,
                    FOREIGN KEY (child_id) REFERENCES children (id)
                )
            ''')
            
            # Новые таблицы для расширенных функций
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sleep_tracker (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    child_id INTEGER NOT NULL,
                    sleep_start TIMESTAMP NOT NULL,
                    sleep_end TIMESTAMP,
                    duration_minutes INTEGER,
                    notes TEXT,
                    FOREIGN KEY (child_id) REFERENCES children (id)
                )
            ''')
            
            # НОВАЯ ТАБЛИЦА: Отслеживание бодрствования
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS wakefulness_tracker (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    child_id INTEGER NOT NULL,
                    wake_start TIMESTAMP NOT NULL,
                    wake_end TIMESTAMP,
                    duration_minutes INTEGER,
                    notes TEXT,
                    FOREIGN KEY (child_id) REFERENCES children (id)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS diaper_tracker (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    child_id INTEGER NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    type TEXT NOT NULL,  -- 'мочеиспускание', 'стул', 'оба'
                    notes TEXT,
                    FOREIGN KEY (child_id) REFERENCES children (id)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS journal_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    child_id INTEGER NOT NULL,
                    note TEXT NOT NULL,
                    category TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (child_id) REFERENCES children (id)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS development_tips (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    age_min_days INTEGER NOT NULL,
                    age_max_days INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    tip_text TEXT NOT NULL,
                    source TEXT
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS vaccination_schedule (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    age_days INTEGER NOT NULL,
                    vaccine_name TEXT NOT NULL,
                    description TEXT,
                    is_mandatory INTEGER DEFAULT 1
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_checklists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    child_id INTEGER NOT NULL,
                    date DATE NOT NULL,
                    feeding_done INTEGER DEFAULT 0,
                    diapers_changed INTEGER DEFAULT 0,
                    sleep_adequate INTEGER DEFAULT 0,
                    tummy_time INTEGER DEFAULT 0,
                    bath_given INTEGER DEFAULT 0,
                    medicines_given INTEGER DEFAULT 0,
                    notes TEXT,
                    FOREIGN KEY (child_id) REFERENCES children (id)
                )
            ''')
            
            conn.commit()
            self._seed_development_tips(conn)
            self._seed_vaccination_schedule(conn)
        finally:
            conn.close()
    
    def _seed_development_tips(self, conn):
        """Заполнить таблицу советами по развитию"""
        cursor = conn.cursor()
        
        # Проверяем, есть ли уже данные
        cursor.execute("SELECT COUNT(*) FROM development_tips")
        if cursor.fetchone()[0] == 0:
            tips = [
                (0, 7, "Уход", "Чаще прикладывайте к груди, следите за мочеиспусканием (6-8 раз в сутки)", "ВОЗ"),
                (0, 7, "Уход", "Поддерживайте температуру в комнате 22-24°C, влажность 40-60%", "Педиатрия"),
                (0, 30, "Здоровье", "Ежедневно обрабатывайте пупочную ранку перекисью и зеленкой", "Минздрав"),
                (0, 30, "Развитие", "Выкладывайте на животик на 1-2 минуты перед кормлением", "Развитие"),
                (30, 60, "Развитие", "Показывайте контрастные картинки, игрушки на расстоянии 20-30 см", "Офтальмология"),
                (60, 90, "Развитие", "Разговаривайте с ребенком, пойте песенки, включайте спокойную музыку", "Неврология"),
                (90, 180, "Питание", "Если на ИВ, можно вводить прикорм с 4 месяцев, но лучше с 6", "Гастроэнтерология"),
                (180, 270, "Развитие", "Давайте ребенку трогать разные текстуры: мягкие, шершавые, гладкие", "Сенсорика"),
                (270, 365, "Развитие", "Играйте в прятки (ку-ку), стройте башни из кубиков", "Психология")
            ]
            cursor.executemany('''
                INSERT INTO development_tips (age_min_days, age_max_days, category, tip_text, source)
                VALUES (?, ?, ?, ?, ?)
            ''', tips)
            conn.commit()
    
    def _seed_vaccination_schedule(self, conn):
        """Заполнить таблицу календаря прививок"""
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM vaccination_schedule")
        if cursor.fetchone()[0] == 0:
            vaccinations = [
                (1, "Гепатит В", "Первая вакцинация", 1),
                (30, "Гепатит В", "Вторая вакцинация (для группы риска)", 1),
                (60, "Пневмококковая", "Первая вакцинация", 1),
                (90, "АКДС", "Первая вакцинация (коклюш, дифтерия, столбняк)", 1),
                (90, "Полиомиелит", "Первая вакцинация", 1),
                (120, "АКДС", "Вторая вакцинация", 1),
                (120, "Полиомиелит", "Вторая вакцинация", 1),
                (150, "АКДС", "Третья вакцинация", 1),
                (150, "Полиомиелит", "Третья вакцинация", 1),
                (180, "Гепатит В", "Третья вакцинация", 1),
                (365, "Корь, краснуха, паротит", "Первая вакцинация", 1)
            ]
            cursor.executemany('''
                INSERT INTO vaccination_schedule (age_days, vaccine_name, description, is_mandatory)
                VALUES (?, ?, ?, ?)
            ''', vaccinations)
            conn.commit()
    
    def get_child(self, chat_id: int) -> Optional[sqlite3.Row]:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM children WHERE chat_id = ?', (chat_id,))
            return cursor.fetchone()
        finally:
            conn.close()
    
    def register_child(self, chat_id: int, child_data: dict) -> int:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO children 
                (chat_id, first_name, last_name, gender, birth_date, gestation_weeks, gestation_days, birth_weight, birth_height)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                chat_id,
                child_data['first_name'],
                child_data['last_name'],
                child_data['gender'],
                child_data['birth_date'],
                child_data['gestation_weeks'],
                child_data['gestation_days'],
                child_data['birth_weight'],
                child_data['birth_height']
            ))
            
            child_id = cursor.lastrowid
            
            reminders = [
                ('weight_height', 1),
                ('weight_height', 7),
                ('weight_height', 30)
            ]
            
            today = datetime.now().date()
            for reminder_type, frequency in reminders:
                cursor.execute('''
                    INSERT INTO reminders 
                    (chat_id, child_id, reminder_type, next_reminder, frequency_days)
                    VALUES (?, ?, ?, ?, ?)
                ''', (chat_id, child_id, reminder_type, today, frequency))
            
            conn.commit()
            return child_id
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    def add_measurement(self, child_id: int, weight: float, height: int):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            
            cursor.execute('SELECT birth_date FROM children WHERE id = ?', (child_id,))
            row = cursor.fetchone()
            if row:
                birth_date_str = row[0]
                if isinstance(birth_date_str, str):
                    birth_date = datetime.strptime(birth_date_str, '%Y-%m-%d').date()
                    age_days = (datetime.now().date() - birth_date).days
                    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    
                    cursor.execute('''
                        INSERT INTO measurements (child_id, weight, height, measurement_date, age_days, recorded_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (child_id, weight, height, datetime.now().date(), age_days, current_time))
                    
                    cursor.execute('''
                        UPDATE reminders 
                        SET next_reminder = date(?, '+' || frequency_days || ' days')
                        WHERE child_id = ? AND reminder_type = 'weight_height' AND is_active = 1
                    ''', (datetime.now().strftime('%Y-%m-%d'), child_id))
            
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    def get_last_measurement(self, child_id: int) -> Optional[sqlite3.Row]:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM measurements 
                WHERE child_id = ? 
                ORDER BY measurement_date DESC, recorded_at DESC 
                LIMIT 1
            ''', (child_id,))
            return cursor.fetchone()
        finally:
            conn.close()
    
    # --- Методы для сна ---
    
    def start_sleep(self, child_id: int) -> int:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO sleep_tracker (child_id, sleep_start)
                VALUES (?, ?)
            ''', (child_id, datetime.now()))
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    def end_sleep(self, sleep_id: int):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('SELECT sleep_start FROM sleep_tracker WHERE id = ?', (sleep_id,))
            row = cursor.fetchone()
            if row:
                sleep_start = datetime.fromisoformat(row[0])
                sleep_end = datetime.now()
                duration = int((sleep_end - sleep_start).total_seconds() / 60)
                
                cursor.execute('''
                    UPDATE sleep_tracker 
                    SET sleep_end = ?, duration_minutes = ?
                    WHERE id = ?
                ''', (sleep_end, duration, sleep_id))
            
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    def get_active_sleep(self, child_id: int) -> Optional[sqlite3.Row]:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM sleep_tracker 
                WHERE child_id = ? AND sleep_end IS NULL
                ORDER BY sleep_start DESC 
                LIMIT 1
            ''', (child_id,))
            return cursor.fetchone()
        finally:
            conn.close()
    
    def get_sleep_stats_today(self, child_id: int):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT 
                    COUNT(*) as sleep_count,
                    SUM(duration_minutes) as total_minutes,
                    AVG(duration_minutes) as avg_minutes
                FROM sleep_tracker 
                WHERE child_id = ? 
                AND DATE(sleep_start) = DATE('now')
                AND sleep_end IS NOT NULL
            ''', (child_id,))
            return cursor.fetchone()
        finally:
            conn.close()
    
    # --- НОВЫЕ МЕТОДЫ: Отслеживание бодрствования ---
    
    def start_wakefulness(self, child_id: int) -> int:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO wakefulness_tracker (child_id, wake_start)
                VALUES (?, ?)
            ''', (child_id, datetime.now()))
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    def end_wakefulness(self, wake_id: int):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('SELECT wake_start FROM wakefulness_tracker WHERE id = ?', (wake_id,))
            row = cursor.fetchone()
            if row:
                wake_start = datetime.fromisoformat(row[0])
                wake_end = datetime.now()
                duration = int((wake_end - wake_start).total_seconds() / 60)
                
                cursor.execute('''
                    UPDATE wakefulness_tracker 
                    SET wake_end = ?, duration_minutes = ?
                    WHERE id = ?
                ''', (wake_end, duration, wake_id))
            
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    def get_active_wakefulness(self, child_id: int) -> Optional[sqlite3.Row]:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM wakefulness_tracker 
                WHERE child_id = ? AND wake_end IS NULL
                ORDER BY wake_start DESC 
                LIMIT 1
            ''', (child_id,))
            return cursor.fetchone()
        finally:
            conn.close()
    
    def get_wakefulness_stats_today(self, child_id: int):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT 
                    COUNT(*) as wake_count,
                    SUM(duration_minutes) as total_minutes,
                    AVG(duration_minutes) as avg_minutes
                FROM wakefulness_tracker 
                WHERE child_id = ? 
                AND DATE(wake_start) = DATE('now')
                AND wake_end IS NOT NULL
            ''', (child_id,))
            return cursor.fetchone()
        finally:
            conn.close()
    
    def add_diaper(self, child_id: int, diaper_type: str):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO diaper_tracker (child_id, type)
                VALUES (?, ?)
            ''', (child_id, diaper_type))
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    def get_diaper_stats_today(self, child_id: int):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT 
                    type,
                    COUNT(*) as count,
                    COUNT(CASE WHEN time(timestamp) > time('now', '-3 hours') THEN 1 END) as recent_count
                FROM diaper_tracker 
                WHERE child_id = ? 
                AND DATE(timestamp) = DATE('now')
                GROUP BY type
            ''', (child_id,))
            return cursor.fetchall()
        finally:
            conn.close()
    
    def add_journal_note(self, child_id: int, note: str, category: str = None):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO journal_notes (child_id, note, category)
                VALUES (?, ?, ?)
            ''', (child_id, note, category))
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    def get_recent_notes(self, child_id: int, limit: int = 5):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM journal_notes 
                WHERE child_id = ? 
                ORDER BY created_at DESC 
                LIMIT ?
            ''', (child_id, limit))
            return cursor.fetchall()
        finally:
            conn.close()
    
    def get_development_tips(self, age_days: int):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM development_tips 
                WHERE age_min_days <= ? AND age_max_days >= ?
                ORDER BY age_min_days
            ''', (age_days, age_days))
            return cursor.fetchall()
        finally:
            conn.close()
    
    def get_vaccination_schedule(self, age_days: int, limit: int = 3):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM vaccination_schedule 
                WHERE age_days >= ?
                ORDER BY age_days ASC
                LIMIT ?
            ''', (age_days, limit))
            return cursor.fetchall()
        finally:
            conn.close()
    
    def update_daily_checklist(self, child_id: int, date: str, field: str, value: int = 1):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            
            # Проверяем, есть ли запись на сегодня
            cursor.execute('SELECT id FROM daily_checklists WHERE child_id = ? AND date = ?', (child_id, date))
            row = cursor.fetchone()
            
            if row:
                # Обновляем существующую запись
                cursor.execute(f'''
                    UPDATE daily_checklists 
                    SET {field} = ?
                    WHERE id = ?
                ''', (value, row[0]))
            else:
                # Создаем новую запись
                cursor.execute(f'''
                    INSERT INTO daily_checklists (child_id, date, {field})
                    VALUES (?, ?, ?)
                ''', (child_id, date, value))
            
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    def get_today_checklist(self, child_id: int):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            today = datetime.now().strftime('%Y-%m-%d')
            cursor.execute('''
                SELECT * FROM daily_checklists 
                WHERE child_id = ? AND date = ?
            ''', (child_id, today))
            return cursor.fetchone()
        finally:
            conn.close()
    
    # --- Существующие методы для кормлений ---
    
    def start_feeding(self, chat_id: int, child_id: int) -> int:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO feedings (chat_id, child_id, start_time)
                VALUES (?, ?, ?)
            ''', (chat_id, child_id, datetime.now()))
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    def update_feeding_prepared(self, feeding_id: int, prepared_ml: int):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE feedings 
                SET prepared_ml = ?
                WHERE id = ?
            ''', (prepared_ml, feeding_id))
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    def pause_feeding(self, feeding_id: int):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE feedings 
                SET is_paused = 1, 
                    paused_at = ?,
                    pauses_count = pauses_count + 1
                WHERE id = ?
            ''', (datetime.now(), feeding_id))
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    def resume_feeding(self, feeding_id: int):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('SELECT paused_at FROM feedings WHERE id = ?', (feeding_id,))
            row = cursor.fetchone()
            if row and row[0]:
                paused_at_str = row[0]
                if paused_at_str:
                    paused_at = datetime.fromisoformat(paused_at_str)
                    pause_duration = int((datetime.now() - paused_at).total_seconds())
                    
                    cursor.execute('''
                        UPDATE feedings 
                        SET is_paused = 0, 
                            paused_at = NULL,
                            total_pause_duration = total_pause_duration + ?
                        WHERE id = ?
                    ''', (pause_duration, feeding_id))
            
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    def add_eaten_ml(self, feeding_id: int, eaten_ml: int):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE feedings 
                SET total_eaten_ml = COALESCE(total_eaten_ml, 0) + ?
                WHERE id = ?
            ''', (eaten_ml, feeding_id))
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    def finish_feeding(self, feeding_id: int):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE feedings 
                SET end_time = ?
                WHERE id = ?
            ''', (datetime.now(), feeding_id))
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    def get_active_feeding(self, chat_id: int) -> Optional[sqlite3.Row]:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM feedings 
                WHERE chat_id = ? AND end_time IS NULL
                ORDER BY start_time DESC 
                LIMIT 1
            ''', (chat_id,))
            return cursor.fetchone()
        finally:
            conn.close()
    
    def get_reminders_due(self):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT r.*, c.first_name, c.chat_id 
                FROM reminders r
                JOIN children c ON r.child_id = c.id
                WHERE r.next_reminder <= date('now') 
                AND r.is_active = 1
            ''')
            return cursor.fetchall()
        finally:
            conn.close()

db = Database()

# --- Глобальные переменные ---
active_feedings = {}

# --- Вспомогательные функции ---
def get_moscow_time() -> datetime:
    return datetime.now(MOSCOW_TZ)

def format_duration(seconds: int) -> str:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}ч {minutes}мин"
    return f"{minutes}мин"

def calculate_age(birth_date: datetime) -> Tuple[int, int, int]:
    today = datetime.now().date()
    birth = birth_date.date()
    
    years = today.year - birth.year
    months = today.month - birth.month
    days = today.day - birth.day
    
    if days < 0:
        months -= 1
        if today.month == 1:
            last_month = 12
            last_year = today.year - 1
        else:
            last_month = today.month - 1
            last_year = today.year
        days_in_last_month = (datetime(last_year, last_month % 12 + 1, 1) - 
                             timedelta(days=1)).day
        days = days_in_last_month + days
    
    if months < 0:
        years -= 1
        months = 12 + months
    
    return years, months, days

def get_daily_checklist_items() -> Dict[str, List[str]]:
    """Возвращает чек-лист для молодых родителей"""
    return {
        "Обязательно": [
            "✓ Проверить температуру (36.6-37.2°C)",
            "✓ Сменить подгузник (8-12 раз в сутки)",
            "✓ Умыть личико, прочистить носик",
            "✓ Проверить кожу на опрелости"
        ],
        "Для новорожденных (0-30 дней)": [
            "✓ Обработать пупочную ранку",
            "✓ Сделать массаж животика по часовой стрелке",
            "✓ Выложить на животик на 2-3 минуты",
            "✓ Обработать кожные складочки"
        ],
        "Для грудничков (1-6 месяцев)": [
            "✓ Гимнастика 5-10 минут",
            "✓ Прогулка на свежем воздухе 1-2 часа",
            "✓ Купание вечером",
            "✓ Игры для развития моторики"
        ]
    }

def calculate_formula(weight_kg: float, age_days: int) -> Dict:
    """Рассчитать суточный объем смеси"""
    if age_days <= 10:
        volume = weight_kg * 70  # 70 мл на кг для новорожденных
    elif age_days <= 60:
        volume = weight_kg * 90  # 90 мл на кг до 2 месяцев
    else:
        volume = weight_kg * 110  # 110 мл на кг после 2 месяцев
    
    feedings_per_day = 8 if age_days > 30 else 10  # Количество кормлений
    per_feeding = volume / feedings_per_day
    
    return {
        "total_ml": round(volume),
        "per_feeding": round(per_feeding),
        "feedings": feedings_per_day
    }

def get_emergency_info() -> str:
    """Информация для экстренных случаев"""
    return """🚨 ЭКСТРЕННАЯ ПОМОЩЬ 🚨

📞 Телефоны:
• 103 - Скорая помощь
• 112 - Единый номер экстренных служб
• 8-800-200-0-200 - Горячая линия Минздрава

⚠️ Когда срочно к врачу:
• Температура выше 38°C у детей до 3 месяцев
• Отказ от еди более 6 часов
• Рвота или понос более 3 раз за час
• Сыпь с температурой
• Затрудненное дыхание, синюшность
• Вялость, отсутствие реакции
• Судороги
• Кровь в стуле или рвоте

💊 Аптечка для новорожденного:
• Жаропонижающее (парацетамол, ибупрофен)
• Солевой раствор для носа
• Антисептик (хлоргексидин)
• Градусник электронный
• Аспиратор назальный
• Вазелиновое масло
• Детский крем от опрелостей
• Ватные диски и палочки с ограничителем

🏥 Что делать до приезда врача:
1. Сохраняйте спокойствие
2. Измерьте температуру
3. Проверьте дыхание
4. Уложите ребенка на бок
5. Соберите документы (полис, СНИЛС)
6. Запишите время начала симптомов"""

def get_development_activities(age_months: int) -> str:
    """Игры и занятия по возрасту"""
    activities = {
        0: "🎯 0-1 месяц: Контрастные картинки (черно-белые), нежные прикосновения, колыбельные, легкий массаб",
        1: "🪀 1-3 месяца: Погремушки, мобиль над кроваткой, безопасное зеркальце, разные текстуры для ощупывания",
        3: "🧸 3-6 месяцев: Развивающий коврик, тканевые книжки, прорезыватели, музыкальные игрушки",
        6: "🏗️ 6-9 месяцев: Пирамидки, сортеры, мячики, кубики, игрушки-каталки, пальчиковые краски",
        9: "📚 9-12 месяцев: Книжки с картинками, матрешки, конструкторы с крупными деталями, кукольный театр"
    }
    
    age_key = max(k for k in activities.keys() if k <= age_months)
    return activities[age_key]

def calculate_medication_dose(weight_kg: float, medication: str) -> str:
    """Рассчитать дозу лекарства по весу"""
    doses = {
        "Парацетамол": {
            "single_dose": weight_kg * 15,  # мг
            "max_daily": weight_kg * 60,  # мг
            "interval_hours": 6,
            "form": "суспензия"
        },
        "Ибупрофен": {
            "single_dose": weight_kg * 10,  # мг
            "max_daily": weight_kg * 30,  # мг
            "interval_hours": 8,
            "form": "суспензия"
        }
    }
    
    if medication in doses:
        dose = doses[medication]
        return f"""💊 {medication} ({dose['form']}):

• Разовая доза: {dose['single_dose']:.0f} мг ({round(dose['single_dose']/100, 1)} мл, если 100 мг/5 мл)
• Максимум в сутки: {dose['max_daily']:.0f} мг
• Интервал: не менее {dose['interval_hours']} часов
• Курс: не более 3 дней без назначения врача

⚠️ Противопоказания: аллергия, тяжелые заболевания печени/почек"""
    
    return f"Препарат '{medication}' не найден в базе. Проконсультируйтесь с врачом."

# --- Клавиатуры ---
def get_main_menu_keyboard() -> types.InlineKeyboardMarkup:
    """Главное меню с красивыми разделами"""
    keyboard = [
        # Раздел 1: Основные операции
        [
            types.InlineKeyboardButton(text="👶 Инфо о ребенке", callback_data="child_info"),
            types.InlineKeyboardButton(text="📊 Параметры", callback_data="update_params")
        ],
        # Раздел 2: Питание и уход
        [
            types.InlineKeyboardButton(text="🍼 Кормление", callback_data="start_feeding"),
            types.InlineKeyboardButton(text="💤 Сон", callback_data="sleep_menu")
        ],
        # Раздел 3: Отслеживание
        [
            types.InlineKeyboardButton(text="🩲 Подгузник", callback_data="diaper_menu"),
            types.InlineKeyboardButton(text="📝 Заметка", callback_data="note_menu")
        ],
        # Раздел 4: Здоровье и развитие
        [
            types.InlineKeyboardButton(text="💊 Лекарства", callback_data="medication_menu"),
            types.InlineKeyboardButton(text="🗓️ Советы", callback_data="tips_menu")
        ],
        # Раздел 5: Статистика и помощь
        [
            types.InlineKeyboardButton(text="📈 Статистика", callback_data="show_stats"),
            types.InlineKeyboardButton(text="🚨 Помощь", callback_data="emergency_help")
        ],
        # Раздел 6: Дополнительные функции
        [
            types.InlineKeyboardButton(text="✅ Чек-лист", callback_data="checklist_menu"),
            types.InlineKeyboardButton(text="🎮 Игры", callback_data="games_menu")
        ]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_feeding_control_keyboard() -> types.InlineKeyboardMarkup:
    """Клавиатура управления кормлением с разделами"""
    keyboard = [
        # Раздел: Добавление еды (добавлена кнопка 5 мл)
        [
            types.InlineKeyboardButton(text="➕ 5 мл", callback_data="add_5"),
            types.InlineKeyboardButton(text="➕ 10 мл", callback_data="add_10"),
            types.InlineKeyboardButton(text="➕ 20 мл", callback_data="add_20")
        ],
        [
            types.InlineKeyboardButton(text="➕ 30 мл", callback_data="add_30"),
            types.InlineKeyboardButton(text="➕ 50 мл", callback_data="add_50"),
            types.InlineKeyboardButton(text="➕ 100 мл", callback_data="add_100")
        ],
        # Раздел: Пользовательский ввод
        [
            types.InlineKeyboardButton(text="📝 Ввести своё количество", callback_data="add_custom")
        ],
        # Раздел: Управление
        [
            types.InlineKeyboardButton(text="⏸️ Пауза", callback_data="pause_feeding"),
            types.InlineKeyboardButton(text="▶️ Продолжить", callback_data="resume_feeding")
        ],
        # Раздел: Завершение
        [
            types.InlineKeyboardButton(text="✅ Завершить", callback_data="finish_feeding"),
            types.InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_feeding")
        ],
        [
            types.InlineKeyboardButton(text="🔙 В главное меню", callback_data="main_menu")
        ]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_paused_feeding_keyboard() -> types.InlineKeyboardMarkup:
    """Клавиатура для приостановленного кормления"""
    keyboard = [
        [
            types.InlineKeyboardButton(text="▶️ Продолжить", callback_data="resume_feeding"),
            types.InlineKeyboardButton(text="✅ Завершить", callback_data="finish_feeding")
        ],
        [
            types.InlineKeyboardButton(text="🔙 В главное меню", callback_data="main_menu")
        ]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_sleep_menu_keyboard() -> types.InlineKeyboardMarkup:
    """Меню отслеживания сна"""
    keyboard = [
        [
            types.InlineKeyboardButton(text="🛏️ Начало сна", callback_data="start_sleep"),
            types.InlineKeyboardButton(text="🌅 Конец сна", callback_data="end_sleep")
        ],
        [
            types.InlineKeyboardButton(text="📊 Статистика сна", callback_data="sleep_stats"),
            types.InlineKeyboardButton(text="🌞 Бодрствование", callback_data="wake_menu")  # НОВАЯ КНОПКА
        ],
        [
            types.InlineKeyboardButton(text="🔙 В главное меню", callback_data="main_menu")
        ]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

# НОВАЯ КЛАВИАТУРА: Меню бодрствования
def get_wake_menu_keyboard() -> types.InlineKeyboardMarkup:
    """Меню отслеживания бодрствования"""
    keyboard = [
        [
            types.InlineKeyboardButton(text="🌞 Начало бодрствования", callback_data="start_wake"),
            types.InlineKeyboardButton(text="🌜 Конец бодрствования", callback_data="end_wake")
        ],
        [
            types.InlineKeyboardButton(text="📊 Статистика бодрствования", callback_data="wake_stats")
        ],
        [
            types.InlineKeyboardButton(text="🔙 Назад к меню сна", callback_data="sleep_menu")
        ]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_diaper_menu_keyboard() -> types.InlineKeyboardMarkup:
    """Меню отслеживания подгузников"""
    keyboard = [
        [
            types.InlineKeyboardButton(text="💦 Мочеиспускание", callback_data="diaper_urine"),
            types.InlineKeyboardButton(text="💩 Стул", callback_data="diaper_poop")
        ],
        [
            types.InlineKeyboardButton(text="💦💩 Оба", callback_data="diaper_both"),
            types.InlineKeyboardButton(text="📊 Статистика", callback_data="diaper_stats")
        ],
        [
            types.InlineKeyboardButton(text="🔙 В главное меню", callback_data="main_menu")
        ]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_gender_keyboard() -> types.InlineKeyboardMarkup:
    """Клавиатура выбора пола"""
    keyboard = [
        [
            types.InlineKeyboardButton(text="👦 Мальчик", callback_data="gender_m"),
            types.InlineKeyboardButton(text="👧 Девочка", callback_data="gender_f")
        ]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

# --- Обработчики нового главного меню ---
@router.callback_query(F.data == "main_menu")
async def main_menu_callback(callback: CallbackQuery):
    """Возврат в главное меню"""
    child = db.get_child(callback.message.chat.id)
    
    text = "🏠 Главное меню\n\n"
    if child:
        years, months, days = calculate_age(datetime.strptime(child['birth_date'], "%Y-%m-%d"))
        text += f"👶 Ребенок: {child['first_name']} {child['last_name'] if child['last_name'] else ''}\n"
        text += f"📅 Возраст: {years} лет, {months} месяцев, {days} дней\n\n"
    
    text += "Выберите раздел:"
    
    if callback.message.text:
        await callback.message.edit_text(text, reply_markup=get_main_menu_keyboard())
    else:
        await callback.message.answer(text, reply_markup=get_main_menu_keyboard())
    await callback.answer()

# --- Обработчики сна ---
@router.callback_query(F.data == "sleep_menu")
async def sleep_menu_callback(callback: CallbackQuery):
    """Меню сна"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка с помощью /register", show_alert=True)
        return
    
    await callback.message.edit_text(
        f"💤 Отслеживание сна и бодрствования\n\n"
        f"👶 Ребенок: {child['first_name']}\n"
        f"📅 Дата: {datetime.now().strftime('%d.%m.%Y')}\n\n"
        "Выберите действие:",
        reply_markup=get_sleep_menu_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "start_sleep")
async def start_sleep_callback(callback: CallbackQuery):
    """Начало сна через inline кнопку"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка с помощью /register", show_alert=True)
        return
    
    active_sleep = db.get_active_sleep(child['id'])
    if active_sleep:
        await callback.answer("Уже есть активный сон! Сначала завершите его.", show_alert=True)
        return
    
    # Если есть активное бодрствование - завершаем его
    active_wake = db.get_active_wakefulness(child['id'])
    if active_wake:
        db.end_wakefulness(active_wake['id'])
    
    sleep_id = db.start_sleep(child['id'])
    
    current_time = datetime.now().strftime("%H:%M")
    await callback.message.edit_text(
        f"🛏️ Сон начат в {current_time}\n"
        f"👶 Для: {child['first_name']}\n\n"
        "Когда ребенок проснется, нажмите '🌅 Конец сна'",
        reply_markup=get_sleep_menu_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "end_sleep")
async def end_sleep_callback(callback: CallbackQuery):
    """Конец сна через inline кнопку"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка с помощью /register", show_alert=True)
        return
    
    active_sleep = db.get_active_sleep(child['id'])
    if not active_sleep:
        await callback.answer("Нет активного сна! Начните сон сначала.", show_alert=True)
        return
    
    db.end_sleep(active_sleep['id'])
    
    sleep_start = datetime.fromisoformat(active_sleep['sleep_start'])
    sleep_end = datetime.now()
    duration = int((sleep_end - sleep_start).total_seconds() / 60)
    
    hours = duration // 60
    minutes = duration % 60
    
    await callback.message.edit_text(
        f"🌅 Сон завершен!\n"
        f"👶 Для: {child['first_name']}\n"
        f"🛏️ Начало: {sleep_start.strftime('%H:%M')}\n"
        f"🌅 Конец: {sleep_end.strftime('%H:%M')}\n"
        f"⏱️ Длительность: {hours}ч {minutes}мин\n\n"
        f"✅ Отлично! Малыши нуждаются в {14-18} часах сна в сутки.",
        reply_markup=get_sleep_menu_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "sleep_stats")
async def sleep_stats_callback(callback: CallbackQuery):
    """Статистика сна через inline кнопку"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка с помощью /register", show_alert=True)
        return
    
    stats = db.get_sleep_stats_today(child['id'])
    
    if stats and stats['sleep_count'] > 0:
        total_hours = stats['total_minutes'] // 60
        total_minutes = stats['total_minutes'] % 60
        avg_hours = stats['avg_minutes'] // 60
        avg_minutes = stats['avg_minutes'] % 60
        
        text = f"📊 Статистика сна за сегодня:\n\n"
        text += f"👶 Ребенок: {child['first_name']}\n"
        text += f"📅 Дата: {datetime.now().strftime('%d.%m.%Y')}\n"
        text += f"🛏️ Количество снов: {stats['sleep_count']}\n"
        text += f"⏱️ Общее время сна: {total_hours}ч {total_minutes}мин\n"
        text += f"📈 Средняя длительность: {avg_hours}ч {avg_minutes}мин\n\n"
        
        # Рекомендации
        age_days = (datetime.now().date() - datetime.strptime(child['birth_date'], "%Y-%m-%d").date()).days
        if age_days <= 90:
            text += "💡 Рекомендация: Новорожденным нужно 14-17 часов сна в сутки"
        elif age_days <= 180:
            text += "💡 Рекомендация: Грудничкам нужно 12-16 часов сна в сутки"
        else:
            text += "💡 Рекомендация: Малышам нужно 11-14 часов сна в сутки"
    else:
        text = "📊 Статистика сна за сегодня:\n\n"
        text += "😴 Данных о сне за сегодня пока нет\n"
        text += "Начните отслеживание с помощью кнопки '🛏️ Начало сна'"
    
    await callback.message.edit_text(
        text,
        reply_markup=get_sleep_menu_keyboard()
    )
    await callback.answer()

# --- НОВЫЕ ОБРАБОТЧИКИ: Отслеживание бодрствования ---
@router.callback_query(F.data == "wake_menu")
async def wake_menu_callback(callback: CallbackQuery):
    """Меню бодрствования"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка с помощью /register", show_alert=True)
        return
    
    await callback.message.edit_text(
        f"🌞 Отслеживание бодрствования\n\n"
        f"👶 Ребенок: {child['first_name']}\n"
        f"📅 Дата: {datetime.now().strftime('%d.%m.%Y')}\n\n"
        "Выберите действие:",
        reply_markup=get_wake_menu_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "start_wake")
async def start_wake_callback(callback: CallbackQuery):
    """Начало бодрствования через inline кнопку"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка с помощью /register", show_alert=True)
        return
    
    active_wake = db.get_active_wakefulness(child['id'])
    if active_wake:
        await callback.answer("Уже есть активное бодрствование! Сначала завершите его.", show_alert=True)
        return
    
    # Если есть активный сон - завершаем его
    active_sleep = db.get_active_sleep(child['id'])
    if active_sleep:
        db.end_sleep(active_sleep['id'])
    
    wake_id = db.start_wakefulness(child['id'])
    
    current_time = datetime.now().strftime("%H:%M")
    await callback.message.edit_text(
        f"🌞 Бодрствование начато в {current_time}\n"
        f"👶 Для: {child['first_name']}\n\n"
        "Когда ребенок начнет засыпать, нажмите '🌜 Конец бодрствования'",
        reply_markup=get_wake_menu_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "end_wake")
async def end_wake_callback(callback: CallbackQuery):
    """Конец бодрствования через inline кнопку"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка с помощью /register", show_alert=True)
        return
    
    active_wake = db.get_active_wakefulness(child['id'])
    if not active_wake:
        await callback.answer("Нет активного бодрствования! Начните бодрствование сначала.", show_alert=True)
        return
    
    db.end_wakefulness(active_wake['id'])
    
    wake_start = datetime.fromisoformat(active_wake['wake_start'])
    wake_end = datetime.now()
    duration = int((wake_end - wake_start).total_seconds() / 60)
    
    hours = duration // 60
    minutes = duration % 60
    
    # Рекомендации по времени бодрствования по возрасту
    age_days = (datetime.now().date() - datetime.strptime(child['birth_date'], "%Y-%m-%d").date()).days
    
    if age_days <= 30:
        recommended_wake = "1-2 часа"
    elif age_days <= 90:
        recommended_wake = "1.5-2.5 часа"
    elif age_days <= 180:
        recommended_wake = "2-3 часа"
    else:
        recommended_wake = "3-4 часа"
    
    await callback.message.edit_text(
        f"🌜 Бодрствование завершено!\n"
        f"👶 Для: {child['first_name']}\n"
        f"🌞 Начало: {wake_start.strftime('%H:%M')}\n"
        f"🌜 Конец: {wake_end.strftime('%H:%M')}\n"
        f"⏱️ Длительность: {hours}ч {minutes}мин\n\n"
        f"💡 Рекомендация: В возрасте {age_days} дней оптимальное время бодрствования: {recommended_wake}",
        reply_markup=get_wake_menu_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "wake_stats")
async def wake_stats_callback(callback: CallbackQuery):
    """Статистика бодрствования через inline кнопку"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка с помощью /register", show_alert=True)
        return
    
    stats = db.get_wakefulness_stats_today(child['id'])
    
    if stats and stats['wake_count'] > 0:
        total_hours = stats['total_minutes'] // 60
        total_minutes = stats['total_minutes'] % 60
        avg_hours = stats['avg_minutes'] // 60
        avg_minutes = stats['avg_minutes'] % 60
        
        text = f"📊 Статистика бодрствования за сегодня:\n\n"
        text += f"👶 Ребенок: {child['first_name']}\n"
        text += f"📅 Дата: {datetime.now().strftime('%d.%m.%Y')}\n"
        text += f"🌞 Количество периодов бодрствования: {stats['wake_count']}\n"
        text += f"⏱️ Общее время бодрствования: {total_hours}ч {total_minutes}мин\n"
        text += f"📈 Средняя длительность: {avg_hours}ч {avg_minutes}мин\n\n"
        
        # Рассчитываем возраст для рекомендаций
        age_days = (datetime.now().date() - datetime.strptime(child['birth_date'], "%Y-%m-%d").date()).days
        
        if age_days <= 30:
            recommended_wake = "1-2 часа"
            daily_sleep = "16-18 часов"
        elif age_days <= 90:
            recommended_wake = "1.5-2.5 часа"
            daily_sleep = "14-16 часов"
        elif age_days <= 180:
            recommended_wake = "2-3 часа"
            daily_sleep = "13-15 часов"
        else:
            recommended_wake = "3-4 часа"
            daily_sleep = "12-14 часов"
        
        text += f"💡 Рекомендации для возраста {age_days} дней:\n"
        text += f"• Время бодрствования: {recommended_wake} за раз\n"
        text += f"• Общий сон в сутки: {daily_sleep}\n"
        text += f"• Обычно 3-4 дневных сна\n\n"
        
        # Проверяем, не переутомился ли ребенок
        if avg_minutes > 240:  # больше 4 часов
            text += "⚠️ Внимание: Слишком долгое бодрствование может привести к переутомлению!"
    else:
        text = "📊 Статистика бодрствования за сегодня:\n\n"
        text += "🌞 Данных о бодрствовании за сегодня пока нет\n"
        text += "Начните отслеживание с помощью кнопки '🌞 Начало бодрствования'"
    
    await callback.message.edit_text(
        text,
        reply_markup=get_wake_menu_keyboard()
    )
    await callback.answer()

# --- Обработчики подгузников ---
@router.callback_query(F.data == "diaper_menu")
async def diaper_menu_callback(callback: CallbackQuery):
    """Меню подгузников"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка с помощью /register", show_alert=True)
        return
    
    await callback.message.edit_text(
        f"🩲 Отслеживание подгузников\n\n"
        f"👶 Ребенок: {child['first_name']}\n"
        f"📅 Дата: {datetime.now().strftime('%d.%m.%Y')}\n\n"
        "Выберите тип:",
        reply_markup=get_diaper_menu_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data.in_(["diaper_urine", "diaper_poop", "diaper_both"]))
async def process_diaper_callback(callback: CallbackQuery):
    """Обработка подгузников через inline кнопки"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка!", show_alert=True)
        return
    
    diaper_type_map = {
        "diaper_urine": "мочеиспускание",
        "diaper_poop": "стул",
        "diaper_both": "оба"
    }
    
    diaper_type = diaper_type_map[callback.data]
    db.add_diaper(child['id'], diaper_type)
    
    # Обновляем чек-лист
    today = datetime.now().strftime('%Y-%m-%d')
    db.update_daily_checklist(child['id'], today, "diapers_changed")
    
    current_time = datetime.now().strftime("%H:%M")
    
    text = f"✅ Подгузник отмечен!\n\n"
    text += f"👶 Ребенок: {child['first_name']}\n"
    text += f"📅 Дата: {datetime.now().strftime('%d.%m.%Y')}\n"
    text += f"⏰ Время: {current_time}\n"
    text += f"🩲 Тип: {diaper_type}\n\n"
    
    # Рекомендации
    if diaper_type == "стул":
        text += "💡 Важно: Стул должен быть желтым, кашицеобразным у грудничков"
    elif diaper_type == "мочеиспускание":
        text += "💡 Норма: 8-12 мочеиспусканий в сутки - признак достаточного питания"
    else:
        text += "💡 Уход: Используйте крем под подгузник для профилактики опрелостей"
    
    await callback.message.edit_text(
        text,
        reply_markup=get_diaper_menu_keyboard()
    )
    await callback.answer("✅ Запись сохранена!")

@router.callback_query(F.data == "diaper_stats")
async def diaper_stats_callback(callback: CallbackQuery):
    """Статистика подгузников через inline кнопку"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка с помощью /register", show_alert=True)
        return
    
    stats = db.get_diaper_stats_today(child['id'])
    
    text = f"📊 Статистика подгузников за сегодня:\n\n"
    text += f"👶 Ребенок: {child['first_name']}\n"
    text += f"📅 Дата: {datetime.now().strftime('%d.%m.%Y')}\n\n"
    
    if stats:
        for row in stats:
            type_emoji = {"мочеиспускание": "💦", "стул": "💩", "оба": "💦💩"}
            emoji = type_emoji.get(row['type'], "🩲")
            text += f"{emoji} {row['type'].title()}: {row['count']} раз\n"
            if row['recent_count']:
                text += f"   (из них за последние 3 часа: {row['recent_count']})\n"
        text += "\n"
        
        # Проверяем нормы
        total = sum(row['count'] for row in stats)
        if total < 6:
            text += "⚠️ Внимание: Мало смен подгузников. Проверьте, достаточно ли ребенок ест.\n"
        elif total > 15:
            text += "⚠️ Внимание: Очень частая смена. Проконсультируйтесь с педиатром.\n"
        else:
            text += "✅ Отлично! Количество смен в пределах нормы.\n"
        
        text += "\n💡 Нормы для грудничков:\n"
        text += "• 8-12 мочеиспусканий в сутки\n"
        text += "• 1-7 стулов в сутки (зависит от типа питания)\n"
    else:
        text += "🩲 Данных за сегодня пока нет\n"
        text += "Начните отслеживание с помощью кнопок выше"
    
    await callback.message.edit_text(
        text,
        reply_markup=get_diaper_menu_keyboard()
    )
    await callback.answer()

# --- Обработчики заметок ---
@router.callback_query(F.data == "note_menu")
async def note_menu_callback(callback: CallbackQuery, state: FSMContext):
    """Меню заметок"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка с помощью /register", show_alert=True)
        return
    
    await callback.message.edit_text(
        f"📝 Журнал заметок\n\n"
        f"👶 Ребенок: {child['first_name']}\n"
        f"📅 Дата: {datetime.now().strftime('%d.%m.%Y')}\n\n"
        "Введите заметку (температура, настроение, особенности поведения, питание и т.д.):"
    )
    await state.set_state(NoteTaking.waiting_for_note)
    await callback.answer()

@router.message(NoteTaking.waiting_for_note)
async def save_note(message: Message, state: FSMContext):
    child = db.get_child(message.chat.id)
    if not child:
        await message.answer("Ребенок не найден!")
        await state.clear()
        return
    
    db.add_journal_note(child['id'], message.text)
    
    # Получаем последние заметки
    recent_notes = db.get_recent_notes(child['id'], 3)
    
    text = "✅ Заметка сохранена!\n\n"
    text += f"📝 Текст: {message.text[:100]}...\n\n"
    
    if recent_notes and len(recent_notes) > 1:
        text += "📋 Последние заметки:\n"
        for i, note in enumerate(recent_notes[:3]):
            date = datetime.fromisoformat(note['created_at']).strftime('%d.%m %H:%M')
            text += f"{i+1}. {date}: {note['note'][:50]}...\n"
    
    await message.answer(text)
    await message.answer("🏠 Главное меню\nВыберите раздел:", reply_markup=get_main_menu_keyboard())
    await state.clear()

# --- Обработчики советов ---
@router.callback_query(F.data == "tips_menu")
async def tips_menu_callback(callback: CallbackQuery):
    """Меню советов"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка с помощью /register", show_alert=True)
        return
    
    # Рассчитываем возраст
    birth_date = datetime.strptime(child['birth_date'], "%Y-%m-%d")
    age_days = (datetime.now().date() - birth_date.date()).days
    age_months = age_days // 30
    
    # Получаем советы из БД
    tips = db.get_development_tips(age_days)
    
    text = f"🗓️ Советы по развитию\n\n"
    text += f"👶 Ребенок: {child['first_name']}\n"
    text += f"📅 Возраст: {age_days} дней ({age_months} месяцев)\n\n"
    
    if tips:
        text += "📚 Рекомендации:\n"
        for tip in tips:
            text += f"• {tip['tip_text']}\n"
            if tip['source']:
                text += f"  *Источник: {tip['source']}*\n"
        text += "\n"
    else:
        text += "Для этого возраста пока нет специальных советов.\n"
        text += "Ребенок развивается индивидуально - следуйте рекомендациям педиатра.\n\n"
    
    # Добавляем календарь прививок
    vaccinations = db.get_vaccination_schedule(age_days, 3)
    if vaccinations:
        text += "💉 Ближайшие прививки:\n"
        for vax in vaccinations:
            days_left = vax['age_days'] - age_days
            if days_left > 0:
                text += f"• Через {days_left} дней: {vax['vaccine_name']}\n"
                if vax['description']:
                    text += f"  {vax['description']}\n"
    
    # Добавляем игры по возрасту
    activities = get_development_activities(age_months)
    text += f"\n🎮 Игры и занятия:\n{activities}"
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="🔙 В главное меню", callback_data="main_menu")]
            ]
        )
    )
    await callback.answer()

# --- Обработчики чек-листа ---
@router.callback_query(F.data == "checklist_menu")
async def checklist_menu_callback(callback: CallbackQuery):
    """Меню чек-листа"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка с помощью /register", show_alert=True)
        return
    
    today = datetime.now().strftime('%Y-%m-%d')
    checklist = db.get_today_checklist(child['id'])
    
    # Рассчитываем возраст
    birth_date = datetime.strptime(child['birth_date'], "%Y-%m-%d")
    age_days = (datetime.now().date() - birth_date.date()).days
    
    text = f"✅ Ежедневный чек-лист\n\n"
    text += f"👶 Ребенок: {child['first_name']}\n"
    text += f"📅 Дата: {datetime.now().strftime('%d.%m.%Y')}\n"
    text += f"🎂 Возраст: {age_days} дней\n\n"
    
    # Общий чек-лист
    checklist_items = get_daily_checklist_items()
    
    for category, items in checklist_items.items():
        text += f"{category}:\n"
        for item in items:
            text += f"{item}\n"
        text += "\n"
    
    # Статус выполненных задач
    if checklist:
        text += "📋 Выполнено сегодня:\n"
        if checklist['feeding_done']:
            text += "✅ Кормления\n"
        if checklist['diapers_changed']:
            text += "✅ Смена подгузников\n"
        if checklist['sleep_adequate']:
            text += "✅ Достаточный сон\n"
        if checklist['tummy_time']:
            text += "✅ Время на животике\n"
        if checklist['bath_given']:
            text += "✅ Купание\n"
        if checklist['medicines_given']:
            text += "✅ Лекарства\n"
    
    # Калькулятор смеси для ИВ
    last_measurement = db.get_last_measurement(child['id'])
    if last_measurement:
        weight_kg = last_measurement['weight'] / 1000
        formula_calc = calculate_formula(weight_kg, age_days)
        
        text += f"\n🍼 Расчет смеси (если на ИВ):\n"
        text += f"• Суточный объем: {formula_calc['total_ml']} мл\n"
        text += f"• За одно кормление: {formula_calc['per_feeding']} мл\n"
        text += f"• Количество кормлений: {formula_calc['feedings']}\n"
    
    text += "\n💡 Совет: Отмечайте выполненные задачи в соответствующих разделах бота"
    
    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="🔙 В главное меню", callback_data="main_menu")]
            ]
        )
    )
    await callback.answer()

# --- Обработчики игр ---
@router.callback_query(F.data == "games_menu")
async def games_menu_callback(callback: CallbackQuery):
    """Меню игр"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка с помощью /register", show_alert=True)
        return
    
    # Рассчитываем возраст
    birth_date = datetime.strptime(child['birth_date'], "%Y-%m-%d")
    age_days = (datetime.now().date() - birth_date.date()).days
    age_months = age_days // 30
    
    activities = get_development_activities(age_months)
    
    text = f"🎮 Развивающие игры и занятия\n\n"
    text += f"👶 Ребенок: {child['first_name']}\n"
    text += f"📅 Возраст: {age_months} месяцев ({age_days} дней)\n\n"
    
    text += activities
    
    text += "\n\n🎯 Общие принципы развития:\n"
    text += "1. Безопасность - все игрушки без мелких деталей\n"
    text += "2. Регулярность - занимайтесь по 5-10 минут несколько раз в день\n"
    text += "3. Наблюдение - следите за реакцией ребенка\n"
    text += "4. Разнообразие - меняйте виды деятельности\n"
    text += "5. Радость - обучение через игру должно приносить удовольствие\n\n"
    
    text += "📚 Полезные материалы:\n"
    text += "• Книги с крупными картинками\n"
    text += "• Музыкальные инструменты (бубен, маракасы)\n"
    text += "• Сенсорные коробки (крупы, вода, песок)\n"
    text += "• Конструкторы с крупными деталями\n"
    text += "• Пальчиковые краски (с 6 месяцев)"
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="🔙 В главное меню", callback_data="main_menu")]
            ]
        )
    )
    await callback.answer()

# --- Обработчики лекарств ---
@router.callback_query(F.data == "medication_menu")
async def medication_menu_callback(callback: CallbackQuery):
    """Меню лекарств"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка с помощью /register", show_alert=True)
        return
    
    await callback.message.edit_text(
        f"💊 Калькулятор дозировок лекарств\n\n"
        f"👶 Ребенок: {child['first_name']}\n\n"
        "Выберите препарат для расчета дозировки:",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(text="💊 Парацетамол", callback_data="med_paracetamol"),
                    types.InlineKeyboardButton(text="💊 Ибупрофен", callback_data="med_ibuprofen")
                ],
                [
                    types.InlineKeyboardButton(text="🔙 В главное меню", callback_data="main_menu")
                ]
            ]
        )
    )
    await callback.answer()

@router.callback_query(F.data.in_(["med_paracetamol", "med_ibuprofen"]))
async def medication_calc_callback(callback: CallbackQuery, state: FSMContext):
    """Расчет лекарств"""
    medication_map = {
        "med_paracetamol": "Парацетамол",
        "med_ibuprofen": "Ибупрофен"
    }
    
    medication = medication_map[callback.data]
    await state.update_data(medication=medication)
    
    await callback.message.edit_text(
        f"💊 {medication}\n\n"
        "Введите вес ребенка в килограммах (например: 8.5):"
    )
    await state.set_state(MedicationTracking.waiting_for_weight_for_med)
    await callback.answer()

@router.message(MedicationTracking.waiting_for_weight_for_med)
async def process_medication_weight(message: Message, state: FSMContext):
    try:
        weight_kg = float(message.text)
        if 2 <= weight_kg <= 30:  # Реалистичный диапазон для ребенка
            data = await state.get_data()
            medication = data['medication']
            
            dose_info = calculate_medication_dose(weight_kg, medication)
            
            text = f"💊 Расчет дозировки\n\n"
            text += f"👶 Вес ребенка: {weight_kg} кг\n"
            text += f"💊 Препарат: {medication}\n\n"
            text += dose_info
            
            # Дополнительные предупреждения
            text += "\n\n⚠️ ВАЖНО:\n"
            text += "• Перед применением проконсультируйтесь с врачом\n"
            text += "• Не превышайте рекомендованные дозы\n"
            text += "• При сохранении температуры более 3 дней - к врачу\n"
            text += "• При аллергических реакциях прекратить прием\n"
            
            await message.answer(text)
            await message.answer("🏠 Главное меню\nВыберите раздел:", reply_markup=get_main_menu_keyboard())
            await state.clear()
        else:
            await message.answer("Введите вес от 2 до 30 кг:")
    except ValueError:
        await message.answer("Введите число (например: 8.5):")

# --- Обработчики экстренной помощи ---
@router.callback_query(F.data == "emergency_help")
async def emergency_help_callback(callback: CallbackQuery):
    """Экстренная помощь"""
    await callback.message.edit_text(
        get_emergency_info(),
        parse_mode="Markdown",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="🔙 В главное меню", callback_data="main_menu")]
            ]
        )
    )
    await callback.answer()

# --- Обработчики быстрого добавления еды ---
@router.callback_query(F.data.in_(["add_5", "add_10", "add_20", "add_30", "add_50", "add_100"]))
async def add_eaten_quick_callback(callback: CallbackQuery):
    """Быстрое добавление съеденного через inline кнопки"""
    chat_id = callback.message.chat.id
    feeding = db.get_active_feeding(chat_id)
    
    if not feeding:
        await callback.answer("Нет активного кормления!", show_alert=True)
        return
    
    ml_map = {
        "add_5": 5,
        "add_10": 10,
        "add_20": 20,
        "add_30": 30,
        "add_50": 50,
        "add_100": 100
    }
    
    eaten_ml = ml_map[callback.data]
    db.add_eaten_ml(feeding['id'], eaten_ml)
    
    child = db.get_child(chat_id)
    if not child:
        await callback.answer("Ребенок не найден!", show_alert=True)
        return
        
    total_eaten = (feeding['total_eaten_ml'] or 0) + eaten_ml
    
    text = (
        f"🍼 Кормление продолжается\n\n"
        f"👶 Ребенок: {child['first_name']}\n"
        f"⏱️ Начало: {datetime.fromisoformat(feeding['start_time']).strftime('%H:%M')}\n"
        f"🍶 Всего съедено: {total_eaten} мл\n\n"
        f"✅ Добавлено: {eaten_ml} мл\n\n"
        "Продолжайте кормить или завершите кормление"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=get_feeding_control_keyboard()
    )
    await callback.answer(f"+{eaten_ml} мл")

# --- Обработчик для ввода произвольного количества ---
@router.callback_query(F.data == "add_custom")
async def add_custom_callback(callback: CallbackQuery, state: FSMContext):
    """Запрос на ввод произвольного количества мл"""
    chat_id = callback.message.chat.id
    feeding = db.get_active_feeding(chat_id)
    
    if not feeding:
        await callback.answer("Нет активного кормления!", show_alert=True)
        return
    
    await callback.message.edit_text(
        "📝 Введите количество мл, которое съел ребенок:\n\n"
        "Введите число (например: 75):"
    )
    await state.set_state(CustomFeedingAmount.waiting_for_custom_amount)
    await callback.answer()

@router.message(CustomFeedingAmount.waiting_for_custom_amount)
async def process_custom_amount(message: Message, state: FSMContext):
    """Обработка введенного произвольного количества мл"""
    chat_id = message.chat.id
    feeding = db.get_active_feeding(chat_id)
    
    if not feeding:
        await message.answer("Нет активного кормления!")
        await state.clear()
        return
    
    try:
        eaten_ml = int(message.text)
        if eaten_ml <= 0:
            await message.answer("Введите положительное число!")
            return
        
        if eaten_ml > 500:  # Реалистичное ограничение
            await message.answer("Введите количество до 500 мл!")
            return
        
        db.add_eaten_ml(feeding['id'], eaten_ml)
        
        child = db.get_child(chat_id)
        if not child:
            await message.answer("Ребенок не найден!")
            await state.clear()
            return
            
        total_eaten = (feeding['total_eaten_ml'] or 0) + eaten_ml
        
        text = (
            f"🍼 Кормление продолжается\n\n"
            f"👶 Ребенок: {child['first_name']}\n"
            f"⏱️ Начало: {datetime.fromisoformat(feeding['start_time']).strftime('%H:%M')}\n"
            f"🍶 Всего съедено: {total_eaten} мл\n\n"
            f"✅ Добавлено: {eaten_ml} мл\n\n"
            "Продолжайте кормить или завершите кормление"
        )
        
        await message.answer(text, reply_markup=get_feeding_control_keyboard())
        await state.clear()
        
    except ValueError:
        await message.answer("Пожалуйста, введите число (например: 75):")

# --- Заглушки для остальных callback-данных ---
@router.callback_query(F.data.in_([
    "temp_tracking", "vaccination_info", "doctor_visit", "medical_record",
    "age_tips", "dev_games", "growth_chart", "gymnastics", "bath_time", "walks",
    "general_stats", "feeding_stats", "weight_chart", "height_chart", 
    "monthly_report", "daily_report", "sleep_history"
]))
async def placeholder_callback(callback: CallbackQuery):
    """Заглушка для пока не реализованных функций"""
    await callback.answer("Эта функция скоро будет доступна! ⏳", show_alert=True)

# --- Команды бота (обновленные) ---
@router.message(CommandStart())
async def start_cmd(message: Message):
    child = db.get_child(message.chat.id)
    
    text = "👶 Бот для отслеживания развития ребенка!\n\n"
    
    if child:
        years, months, days = calculate_age(datetime.strptime(child['birth_date'], "%Y-%m-%d"))
        text += f"👶 Ребенок: {child['first_name']} {child['last_name'] if child['last_name'] else ''}\n"
        text += f"📅 Дата рождения: {child['birth_date']}\n"
        text += f"🎂 Возраст: {years} лет, {months} месяцев, {days} дней\n\n"
        
    await message.answer(
        text,
        parse_mode="Markdown"
    )
    
    await message.answer(
        "🏠 Главное меню\nВыберите раздел:",
        reply_markup=get_main_menu_keyboard()
    )

@router.message(Command("menu"))
async def menu_cmd(message: Message):
    """Команда для вызова главного меню"""
    await message.answer(
        "🏠 Главное меню\nВыберите раздел:",
        reply_markup=get_main_menu_keyboard()
    )

@router.message(Command("help"))
async def help_cmd(message: Message):
    help_text = """📋 Доступные команды и функции:

Основные:
/start - Главное меню
/register - Регистрация ребенка
/child_info - Информация о ребенке
/params - Внести параметры роста/веса
/stats - Статистика развития
/menu - Главное меню (inline)
/help - Справка

Новые функции для родителей:
• 💤 Сон - Трекер сна
• 🌞 Бодрствование - Трекер времени бодрствования
• 🩲 Подгузник - Трекер смены подгузников
• 📝 Заметка - Журнал для записей
• 🗓️ Советы - Рекомендации по возрасту
• 🚨 Помощь - Экстренная информация
• 💊 Лекарства - Калькулятор дозировок
• ✅ Чек-лист - Ежедневные задачи
• 🎮 Игры - Развивающие занятия

Для кормлений:
/feeding - Начать кормление
/add_eaten - Добавить съеденное
/pause - Приостановить кормление
/resume - Продолжить кормление
/finish - Завершить кормление"""
    
    await message.answer(help_text)

# --- Существующие обработчики (без изменений) ---
@router.message(Command("register"))
async def register_child_cmd(message: Message, state: FSMContext):
    child = db.get_child(message.chat.id)
    if child:
        await message.answer("Ребенок уже зарегистрирован! Используйте /child_info для просмотра данных.")
        return
    await message.answer("Введите имя ребенка:")
    await state.set_state(ChildRegistration.waiting_for_first_name)

@router.message(ChildRegistration.waiting_for_first_name)
async def process_first_name(message: Message, state: FSMContext):
    await state.update_data(first_name=message.text)
    await message.answer("Введите фамилию ребенка (или напишите '-' если нет):")
    await state.set_state(ChildRegistration.waiting_for_last_name)

@router.message(ChildRegistration.waiting_for_last_name)
async def process_last_name(message: Message, state: FSMContext):
    last_name = message.text if message.text != '-' else ''
    await state.update_data(last_name=last_name)
    await message.answer("Выберите пол ребенка:", reply_markup=get_gender_keyboard())
    await state.set_state(ChildRegistration.waiting_for_gender)

@router.callback_query(ChildRegistration.waiting_for_gender, F.data.startswith("gender_"))
async def process_gender(callback: CallbackQuery, state: FSMContext):
    gender = "М" if callback.data == "gender_m" else "Ж"
    await state.update_data(gender=gender)
    await callback.message.answer("Введите дату рождения в формате ДД.ММ.ГГГГ:")
    await state.set_state(ChildRegistration.waiting_for_birth_date)
    await callback.answer()

@router.message(ChildRegistration.waiting_for_birth_date)
async def process_birth_date(message: Message, state: FSMContext):
    try:
        birth_date = datetime.strptime(message.text, "%d.%m.%Y")
        await state.update_data(birth_date=birth_date.strftime("%Y-%m-%d"))
        await message.answer("Введите срок беременности (недели от 20 до 42):")
        await state.set_state(ChildRegistration.waiting_for_gestation_weeks)
    except ValueError:
        await message.answer("Неверный формат даты. Введите в формате ДД.ММ.ГГГГ:")

@router.message(ChildRegistration.waiting_for_gestation_weeks)
async def process_gestation_weeks(message: Message, state: FSMContext):
    try:
        weeks = int(message.text)
        if 20 <= weeks <= 42:
            await state.update_data(gestation_weeks=weeks)
            await message.answer("Введите дополнительные дни срока (0-6):")
            await state.set_state(ChildRegistration.waiting_for_gestation_days)
        else:
            await message.answer("Введите число от 20 до 42:")
    except ValueError:
        await message.answer("Введите число от 20 до 42:")

@router.message(ChildRegistration.waiting_for_gestation_days)
async def process_gestation_days(message: Message, state: FSMContext):
    try:
        days = int(message.text)
        if 0 <= days <= 6:
            await state.update_data(gestation_days=days)
            await message.answer("Введите вес при рождении (в граммах, например: 3500):")
            await state.set_state(ChildRegistration.waiting_for_birth_weight)
        else:
            await message.answer("Введите число от 0 до 6:")
    except ValueError:
        await message.answer("Введите число от 0 до 6:")

@router.message(ChildRegistration.waiting_for_birth_weight)
async def process_birth_weight(message: Message, state: FSMContext):
    try:
        weight = float(message.text)
        if 500 <= weight <= 6000:
            await state.update_data(birth_weight=weight)
            await message.answer("Введите рост при рождении (в см, например: 52):")
            await state.set_state(ChildRegistration.waiting_for_birth_height)
        else:
            await message.answer("Введите вес от 500 до 6000 грамм:")
    except ValueError:
        await message.answer("Введите число (например: 3500):")

@router.message(ChildRegistration.waiting_for_birth_height)
async def process_birth_height(message: Message, state: FSMContext):
    try:
        height = int(message.text)
        if 30 <= height <= 70:
            data = await state.get_data()
            data['birth_height'] = height
            
            child_id = db.register_child(message.chat.id, data)
            
            if child_id:
                years, months, days = calculate_age(datetime.strptime(data['birth_date'], "%Y-%m-%d"))
                
                text = (
                    "✅ Ребенок успешно зарегистрирован!\n\n"
                    f"👶 Имя: {data['first_name']} {data['last_name'] if data['last_name'] else ''}\n"
                    f"🚻 Пол: {data['gender']}\n"
                    f"📅 Дата рождения: {data['birth_date']}\n"
                    f"🎂 Возраст: {years} лет, {months} месяцев, {days} дней\n"
                    f"🤰 Срок беременности: {data['gestation_weeks']} недель {data['gestation_days']} дней\n"
                    f"⚖️ Вес при рождении: {data['birth_weight']} г\n"
                    f"📏 Рост при рождении: {data['birth_height']} см\n\n"
                    "Теперь вы можете начать отслеживать кормления и параметры развития."
                )
                
                await message.answer(text)
                await message.answer("🏠 Главное меню\nВыберите раздел:", reply_markup=get_main_menu_keyboard())
                await state.clear()
                
                db.add_measurement(child_id, data['birth_weight'], data['birth_height'])
            else:
                await message.answer("Ошибка регистрации ребенка. Попробуйте еще раз.")
        else:
            await message.answer("Введите рост от 30 до 70 см:")
    except ValueError:
        await message.answer("Введите число (например: 52):")

@router.message(Command("child_info"))
async def child_info_cmd(message: Message):
    child = db.get_child(message.chat.id)
    if not child:
        await message.answer("Ребенок не зарегистрирован. Используйте /register")
        return
    
    years, months, days = calculate_age(datetime.strptime(child['birth_date'], "%Y-%m-%d"))
    last_measurement = db.get_last_measurement(child['id'])
    
    text = (
        f"👶 Информация о ребенке\n\n"
        f"👶 Ребенок: {child['first_name']} {child['last_name'] if child['last_name'] else ''}\n"
        f"🚻 Пол: {child['gender']}\n"
        f"📅 Дата рождения: {child['birth_date']}\n"
        f"🎂 Возраст: {years} лет, {months} месяцев, {days} дней\n"
        f"🤰 Срок беременности: {child['gestation_weeks']} нед. {child['gestation_days']} дн.\n"
        f"⚖️ Вес при рождении: {child['birth_weight']} г\n"
        f"📏 Рост при рождении: {child['birth_height']} см\n"
    )
    
    if last_measurement:
        weight_gain = last_measurement['weight'] - child['birth_weight']
        height_gain = last_measurement['height'] - child['birth_height']
        
        text += (
            f"\n📊 Последние измерения:\n"
            f"⚖️ Вес: {last_measurement['weight']} г (+{weight_gain} г)\n"
            f"📏 Рост: {last_measurement['height']} см (+{height_gain} см)\n"
            f"📅 Дата: {last_measurement['measurement_date']}\n"
            f"🎂 Возраст на момент измерения: {last_measurement['age_days']} дней"
        )
    
    await message.answer(text)
    await message.answer("🏠 Главное меню\nВыберите раздел:", reply_markup=get_main_menu_keyboard())

@router.message(Command("params"))
async def params_cmd(message: Message, state: FSMContext):
    child = db.get_child(message.chat.id)
    if not child:
        await message.answer("Сначала зарегистрируйте ребенка с помощью /register")
        return
    
    await message.answer("Введите текущий вес ребенка в граммах (например: 4500):")
    await state.set_state(UpdateParams.waiting_for_weight)

@router.message(UpdateParams.waiting_for_weight)
async def process_weight(message: Message, state: FSMContext):
    try:
        weight = float(message.text)
        if 500 <= weight <= 20000:
            await state.update_data(weight=weight)
            await message.answer("Введите текущий рост в см (например: 60):")
            await state.set_state(UpdateParams.waiting_for_height)
        else:
            await message.answer("Введите вес от 500 до 20000 грамм:")
    except ValueError:
        await message.answer("Введите число (например: 4500):")

@router.message(UpdateParams.waiting_for_height)
async def process_height(message: Message, state: FSMContext):
    try:
        height = int(message.text)
        if 30 <= height <= 120:
            child = db.get_child(message.chat.id)
            if not child:
                await message.answer("Ребенок не найден!")
                await state.clear()
                return
                
            data = await state.get_data()
            
            db.add_measurement(child['id'], data['weight'], height)
            
            last_measurement = db.get_last_measurement(child['id'])
            
            text = "✅ Параметры успешно сохранены!\n\n"
            if last_measurement:
                text += (
                    f"⚖️ Вес: {data['weight']} г\n"
                    f"📏 Рост: {height} см\n"
                    f"📅 Дата измерения: {datetime.now().strftime('%d.%m.%Y')}"
                )
            
            await message.answer(text)
            await message.answer("🏠 Главное меню\nВыберите раздел:", reply_markup=get_main_menu_keyboard())
            await state.clear()
        else:
            await message.answer("Введите рост от 30 до 120 см:")
    except ValueError:
        await message.answer("Введите число (например: 60):")

# --- Обработчики кормления через callback ---
@router.callback_query(F.data == "start_feeding")
async def start_feeding_callback(callback: CallbackQuery):
    if not callback.message:
        await callback.answer()
        return
        
    chat_id = callback.message.chat.id
    child = db.get_child(chat_id)
    
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка с помощью /register", show_alert=True)
        return
    
    active_feeding = db.get_active_feeding(chat_id)
    if active_feeding:
        await callback.answer("Уже есть активное кормление!", show_alert=True)
        return
    
    feeding_id = db.start_feeding(chat_id, child['id'])
    
    text = (
        f"🍼 Кормление начато!\n\n"
        f"👶 Ребенок: {child['first_name']}\n"
        f"⏱️ Начало: {datetime.now().strftime('%H:%M')}\n"
        f"🍶 Съедено: 0 мл\n\n"
        "Добавляйте съеденное по мере кормления:"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=get_feeding_control_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "pause_feeding")
async def pause_feeding_callback(callback: CallbackQuery):
    if not callback.message:
        await callback.answer()
        return
        
    chat_id = callback.message.chat.id
    feeding = db.get_active_feeding(chat_id)
    
    if not feeding:
        await callback.answer("Нет активного кормления!", show_alert=True)
        return
    
    if feeding['is_paused']:
        await callback.answer("Кормление уже на паузе!", show_alert=True)
        return
    
    db.pause_feeding(feeding['id'])
    
    text = (
        f"⏸️ Кормление приостановлено\n\n"
        f"👶 Ребенок: {db.get_child(chat_id)['first_name']}\n"
        f"⏱️ На паузе с: {datetime.now().strftime('%H:%M')}\n"
        f"🍶 Съедено: {feeding['total_eaten_ml'] or 0} мл\n\n"
        "Когда ребенок будет готов продолжить, нажмите '▶️ Продолжить'"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=get_paused_feeding_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "resume_feeding")
async def resume_feeding_callback(callback: CallbackQuery):
    if not callback.message:
        await callback.answer()
        return
        
    chat_id = callback.message.chat.id
    feeding = db.get_active_feeding(chat_id)
    
    if not feeding or not feeding['is_paused']:
        await callback.answer("Нет кормления на паузе!", show_alert=True)
        return
    
    db.resume_feeding(feeding['id'])
    
    child = db.get_child(chat_id)
    text = (
        f"🍼 Кормление продолжено!\n\n"
        f"👶 Ребенок: {child['first_name']}\n"
        f"⏱️ Продолжено в: {datetime.now().strftime('%H:%M')}\n"
        f"🍶 Съедено: {feeding['total_eaten_ml'] or 0} мл\n\n"
        "Продолжайте кормить ребенка"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=get_feeding_control_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "finish_feeding")
async def finish_feeding_callback(callback: CallbackQuery):
    if not callback.message:
        await callback.answer()
        return
        
    chat_id = callback.message.chat.id
    feeding = db.get_active_feeding(chat_id)
    
    if not feeding:
        await callback.answer("Нет активного кормления!", show_alert=True)
        return
    
    db.finish_feeding(feeding['id'])
    
    child = db.get_child(chat_id)
    start_time = datetime.fromisoformat(feeding['start_time'])
    end_time = datetime.now()
    duration = end_time - start_time
    
    total_duration_seconds = int(duration.total_seconds()) - (feeding['total_pause_duration'] or 0)
    
    text = (
        f"✅ Кормление завершено!\n\n"
        f"👶 Ребенок: {child['first_name']}\n"
        f"⏱️ Начало: {start_time.strftime('%H:%M')}\n"
        f"⏱️ Конец: {end_time.strftime('%H:%M')}\n"
        f"⏳ Длительность: {format_duration(total_duration_seconds)}\n"
        f"⏸️ Пауз: {feeding['pauses_count'] or 0}\n"
        f"🍶 Всего съедено: {feeding['total_eaten_ml'] or 0} мл"
    )
    
    if feeding['prepared_ml']:
        text += f"\n🍶 Приготовлено: {feeding['prepared_ml']} мл"
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")]
            ]
        )
    )
    await callback.answer()

@router.callback_query(F.data == "cancel_feeding")
async def cancel_feeding_callback(callback: CallbackQuery):
    if not callback.message:
        await callback.answer()
        return
        
    chat_id = callback.message.chat.id
    feeding = db.get_active_feeding(chat_id)
    
    if not feeding:
        await callback.answer("Нет активного кормления!", show_alert=True)
        return
    
    with sqlite3.connect(db.db_name) as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM feedings WHERE id = ?', (feeding['id'],))
    
    await callback.message.edit_text(
        "❌ Кормление отменено",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")]
            ]
        )
    )
    await callback.answer()

# --- Обработчики параметров через callback ---
@router.callback_query(F.data == "update_params")
async def update_params_callback(callback: CallbackQuery, state: FSMContext):
    if not callback.message:
        await callback.answer()
        return
        
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка!", show_alert=True)
        return
    
    await callback.message.edit_text(
        f"📊 Внесение параметров\n\n"
        f"👶 Ребенок: {child['first_name']}\n\n"
        "Введите текущий вес ребенка в граммах (например: 4500):"
    )
    await state.set_state(UpdateParams.waiting_for_weight)
    await callback.answer()

# --- Обработчики статистики через callback ---
@router.callback_query(F.data == "show_stats")
async def show_stats_callback(callback: CallbackQuery):
    if not callback.message:
        await callback.answer()
        return
        
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка!", show_alert=True)
        return
    
    await show_stats_dialog(callback.message)
    await callback.answer()

async def show_stats_dialog(message: Message):
    child = db.get_child(message.chat.id)
    if not child:
        await message.answer("Сначала зарегистрируйте ребенка с помощью команды /register")
        return
    
    with sqlite3.connect(db.db_name) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                date(start_time) as feeding_date,
                COUNT(*) as feedings_count,
                SUM(total_eaten_ml) as total_ml
            FROM feedings 
            WHERE child_id = ? 
            AND date(start_time) >= date('now', '-7 days')
            GROUP BY date(start_time)
            ORDER BY feeding_date DESC
        ''', (child['id'],))
        
        feedings_stats = cursor.fetchall()
        
        cursor.execute('''
            SELECT weight, height, measurement_date, recorded_at
            FROM measurements
            WHERE child_id = ?
            ORDER BY measurement_date DESC, recorded_at DESC
            LIMIT 5
        ''', (child['id'],))
        
        measurements = cursor.fetchall()
    
    text = f"📊 Статистика для {child['first_name']}\n\n"
    
    if feedings_stats:
        text += "🍼 Кормления за последние 7 дней:\n"
        for stat in feedings_stats:
            text += f"  📅 {stat['feeding_date']}: {stat['feedings_count']} кормлений, {stat['total_ml'] or 0} мл\n"
        text += "\n"
    else:
        text += "😴 Нет данных о кормлениях за последние 7 дней\n\n"
    
    if measurements:
        text += "📈 Динамика параметров:\n"
        for i, m in enumerate(measurements):
            recorded_time = ""
            if m['recorded_at']:
                try:
                    if isinstance(m['recorded_at'], str):
                        dt = datetime.fromisoformat(m['recorded_at'].replace('Z', '+00:00'))
                        recorded_time = f" ({dt.strftime('%H:%M')})"
                except:
                    pass
            
            if i == 0:
                text += f"  📅 {m['measurement_date']}{recorded_time}: {m['weight']} г, {m['height']} см (последнее)\n"
            else:
                text += f"  📅 {m['measurement_date']}{recorded_time}: {m['weight']} г, {m['height']} см\n"
    else:
        text += "📏 Нет данных об измерениях\n"
    
    # Добавляем статистику сна и бодрствования
    sleep_stats = db.get_sleep_stats_today(child['id'])
    wake_stats = db.get_wakefulness_stats_today(child['id'])
    diaper_stats = db.get_diaper_stats_today(child['id'])
    
    if sleep_stats and sleep_stats['sleep_count']:
        total_hours = sleep_stats['total_minutes'] // 60
        total_minutes = sleep_stats['total_minutes'] % 60
        text += f"\n💤 Сон сегодня: {sleep_stats['sleep_count']} раз, {total_hours}ч {total_minutes}мин"
    
    if wake_stats and wake_stats['wake_count']:
        total_hours = wake_stats['total_minutes'] // 60
        total_minutes = wake_stats['total_minutes'] % 60
        text += f"\n🌞 Бодрствование сегодня: {wake_stats['wake_count']} раз, {total_hours}ч {total_minutes}мин"
    
    if diaper_stats:
        text += f"\n🩲 Подгузники сегодня: "
        for row in diaper_stats:
            type_emoji = {"мочеиспускание": "💦", "стул": "💩", "оба": "💦💩"}
            emoji = type_emoji.get(row['type'], "🩲")
            text += f"{emoji}{row['count']} "
    
    await message.answer(text)
    await message.answer("🏠 Главное меню\nВыберите раздел:", reply_markup=get_main_menu_keyboard())

# --- Обработчики информации о ребенке через callback ---
@router.callback_query(F.data == "child_info")
async def child_info_callback(callback: CallbackQuery):
    if not callback.message:
        await callback.answer()
        return
        
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Ребенок не зарегистрирован. Используйте /register", show_alert=True)
        return
    
    years, months, days = calculate_age(datetime.strptime(child['birth_date'], "%Y-%m-%d"))
    last_measurement = db.get_last_measurement(child['id'])
    
    text = (
        f"👶 Информация о ребенке\n\n"
        f"👶 Ребенок: {child['first_name']} {child['last_name'] if child['last_name'] else ''}\n"
        f"🚻 Пол: {child['gender']}\n"
        f"📅 Дата рождения: {child['birth_date']}\n"
        f"🎂 Возраст: {years} лет, {months} месяцев, {days} дней\n"
        f"🤰 Срок беременности: {child['gestation_weeks']} нед. {child['gestation_days']} дн.\n"
        f"⚖️ Вес при рождении: {child['birth_weight']} г\n"
        f"📏 Рост при рождении: {child['birth_height']} см\n"
    )
    
    if last_measurement:
        weight_gain = last_measurement['weight'] - child['birth_weight']
        height_gain = last_measurement['height'] - child['birth_height']
        
        text += (
            f"\n📊 Последние измерения:\n"
            f"⚖️ Вес: {last_measurement['weight']} г (+{weight_gain} г)\n"
            f"📏 Рост: {last_measurement['height']} см (+{height_gain} см)\n"
            f"📅 Дата: {last_measurement['measurement_date']}\n"
            f"🎂 Возраст на момент измерения: {last_measurement['age_days']} дней"
        )
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")]
            ]
        )
    )
    await callback.answer()

# --- Система напоминаний ---
async def check_reminders():
    while True:
        try:
            reminders = db.get_reminders_due()
            for reminder in reminders:
                child = db.get_child(reminder['chat_id'])
                if child:
                    birth_date = datetime.strptime(child['birth_date'], "%Y-%m-%d")
                    age_days = (datetime.now().date() - birth_date.date()).days
                    
                    if age_days <= 14:
                        frequency_text = "ежедневно"
                    elif age_days <= 90:
                        frequency_text = "еженедельно"
                    else:
                        frequency_text = "ежемесячно"
                    
                    text = (
                        f"🔔 Напоминание для {child['first_name']}\n\n"
                        f"Пора измерить параметры развития ребенка!\n"
                        f"📅 Возраст: {age_days} дней\n"
                        f"📋 Рекомендуемая частота: {frequency_text}\n\n"
                        f"Используйте кнопку '📊 Параметры' для внесения данных."
                    )
                    
                    await bot.send_message(reminder['chat_id'], text)
            
            await asyncio.sleep(24 * 60 * 60)
        except Exception as e:
            logger.error(f"Ошибка в проверке напоминаний: {e}")
            await asyncio.sleep(60 * 60)

# --- Запуск бота ---
async def main():
    logger.info("Бот запущен!")
    
    # Удаляем вебхук перед запуском поллинга
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Вебхук удален, запускаем поллинг...")
    except Exception as e:
        logger.error(f"Ошибка при удалении вебхука: {e}")
    
    asyncio.create_task(check_reminders())
    
    await dp.start_polling(bot)

if __name__ == "__main__":

    asyncio.run(main())
