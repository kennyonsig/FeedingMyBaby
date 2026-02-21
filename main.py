import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any, Union, Tuple
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

# Загружаем переменные окружения из файла .env
load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация
MOSCOW_TZ = pytz.timezone('Europe/Moscow')
DB_NAME = 'baby_tracker.db'
API_TOKEN = os.getenv('API_TOKEN')

# Проверяем наличие токена
if not API_TOKEN:
    logger.error("API_TOKEN не найден в переменных окружения!")
    raise ValueError("API_TOKEN не задан")

# Инициализация бота
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

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
    waiting_for_cancel = State()

class UpdateParams(StatesGroup):
    waiting_for_weight = State()
    waiting_for_height = State()
    waiting_for_cancel = State()

class SleepTracking(StatesGroup):
    waiting_for_sleep_type = State()
    waiting_for_cancel = State()

class DiaperTracking(StatesGroup):
    waiting_for_diaper_type = State()
    waiting_for_cancel = State()

class NoteTaking(StatesGroup):
    waiting_for_note = State()
    waiting_for_cancel = State()

class CustomFeedingAmount(StatesGroup):
    waiting_for_custom_amount = State()
    waiting_for_cancel = State()

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
            
            # Таблица детей
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
            
            # Таблица кормлений
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
            
            # Таблица измерений (вес/рост)
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
            
            # Таблица напоминаний
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
            
            # Таблица сна
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
            
            # Таблица бодрствования
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
            
            # Таблица подгузников
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS diaper_tracker (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    child_id INTEGER NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    type TEXT NOT NULL,
                    notes TEXT,
                    FOREIGN KEY (child_id) REFERENCES children (id)
                )
            ''')
            
            # Таблица заметок
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
            
            conn.commit()
        finally:
            conn.close()
    
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
            
            today = get_moscow_time().date()
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
                    age_days = (get_moscow_time().date() - birth_date).days
                    current_time = get_moscow_time()
                    
                    cursor.execute('''
                        INSERT INTO measurements (child_id, weight, height, measurement_date, age_days, recorded_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (child_id, weight, height, get_moscow_time().date(), age_days, current_time))
                    
                    cursor.execute('''
                        UPDATE reminders 
                        SET next_reminder = date(?, '+' || frequency_days || ' days')
                        WHERE child_id = ? AND reminder_type = 'weight_height' AND is_active = 1
                    ''', (get_moscow_time().strftime('%Y-%m-%d'), child_id))
            
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
            ''', (child_id, get_moscow_time()))
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
                sleep_end = get_moscow_time()
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
            today_str = get_moscow_time().strftime('%Y-%m-%d')
            cursor.execute('''
                SELECT 
                    COUNT(*) as sleep_count,
                    SUM(duration_minutes) as total_minutes,
                    AVG(duration_minutes) as avg_minutes
                FROM sleep_tracker 
                WHERE child_id = ? 
                AND DATE(sleep_start) = ?
                AND sleep_end IS NOT NULL
            ''', (child_id, today_str))
            return cursor.fetchone()
        finally:
            conn.close()
    
    # --- Методы для бодрствования ---
    def start_wakefulness(self, child_id: int) -> int:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO wakefulness_tracker (child_id, wake_start)
                VALUES (?, ?)
            ''', (child_id, get_moscow_time()))
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
                wake_end = get_moscow_time()
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
            today_str = get_moscow_time().strftime('%Y-%m-%d')
            cursor.execute('''
                SELECT 
                    COUNT(*) as wake_count,
                    SUM(duration_minutes) as total_minutes,
                    AVG(duration_minutes) as avg_minutes
                FROM wakefulness_tracker 
                WHERE child_id = ? 
                AND DATE(wake_start) = ?
                AND wake_end IS NOT NULL
            ''', (child_id, today_str))
            return cursor.fetchone()
        finally:
            conn.close()
    
    # --- Методы для подгузников ---
    def add_diaper(self, child_id: int, diaper_type: str):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO diaper_tracker (child_id, type, timestamp)
                VALUES (?, ?, ?)
            ''', (child_id, diaper_type, get_moscow_time()))
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
            today_str = get_moscow_time().strftime('%Y-%m-%d')
            cursor.execute('''
                SELECT 
                    type,
                    COUNT(*) as count,
                    COUNT(CASE WHEN time(timestamp) > time('now', '-3 hours') THEN 1 END) as recent_count
                FROM diaper_tracker 
                WHERE child_id = ? 
                AND DATE(timestamp) = ?
                GROUP BY type
            ''', (child_id, today_str))
            return cursor.fetchall()
        finally:
            conn.close()
    
    # --- Методы для заметок ---
    def add_journal_note(self, child_id: int, note: str, category: str = None):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO journal_notes (child_id, note, category, created_at)
                VALUES (?, ?, ?, ?)
            ''', (child_id, note, category, get_moscow_time()))
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
    
    # --- Методы для кормлений ---
    def get_daily_feeding_stats(self, child_id: int):
        """Возвращает количество кормлений и суммарный объём за сегодня (по МСК)"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            today_str = get_moscow_time().strftime('%Y-%m-%d')
            cursor.execute('''
                SELECT 
                    COUNT(*) as feedings_count,
                    COALESCE(SUM(total_eaten_ml), 0) as total_ml
                FROM feedings 
                WHERE child_id = ? 
                AND DATE(start_time) = ?
            ''', (child_id, today_str))
            return cursor.fetchone()
        finally:
            conn.close()

    def get_today_feedings(self, child_id: int):
        """Возвращает список кормлений за сегодня с временем и объёмом"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            today_str = get_moscow_time().strftime('%Y-%m-%d')
            cursor.execute('''
                SELECT 
                    time(start_time) as start_time,
                    time(end_time) as end_time,
                    total_eaten_ml
                FROM feedings 
                WHERE child_id = ? 
                AND DATE(start_time) = ?
                AND end_time IS NOT NULL
                ORDER BY start_time ASC
            ''', (child_id, today_str))
            return cursor.fetchall()
        finally:
            conn.close()
    
    def start_feeding(self, chat_id: int, child_id: int) -> int:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO feedings (chat_id, child_id, start_time)
                VALUES (?, ?, ?)
            ''', (chat_id, child_id, get_moscow_time()))
            conn.commit()
            return cursor.lastrowid
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
            ''', (get_moscow_time(), feeding_id))
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
    
    def delete_active_feeding(self, chat_id: int):
        """Удаляет активное кормление (защита от багов)"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                DELETE FROM feedings 
                WHERE chat_id = ? AND end_time IS NULL
            ''', (chat_id,))
            conn.commit()
            return cursor.rowcount
        except Exception as e:
            conn.rollback()
            raise e
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
    """Возвращает наивное (без часового пояса) московское время"""
    return datetime.now(MOSCOW_TZ).replace(tzinfo=None)

def format_duration(seconds: int) -> str:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}ч {minutes}мин"
    return f"{minutes}мин"

def calculate_age(birth_date: datetime) -> Tuple[int, int, int]:
    today = get_moscow_time().date()
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

# --- Клавиатуры ---
def get_main_menu_keyboard() -> types.InlineKeyboardMarkup:
    """Главное меню"""
    keyboard = [
        [
            types.InlineKeyboardButton(text="👶 Инфо о ребенке", callback_data="child_info"),
            types.InlineKeyboardButton(text="📊 Параметры", callback_data="update_params")
        ],
        [
            types.InlineKeyboardButton(text="🍼 Кормление", callback_data="start_feeding"),
            types.InlineKeyboardButton(text="💤 Сон", callback_data="sleep_menu")
        ],
        [
            types.InlineKeyboardButton(text="🩲 Подгузник", callback_data="diaper_menu"),
            types.InlineKeyboardButton(text="📝 Заметка", callback_data="note_menu")
        ],
        [
            types.InlineKeyboardButton(text="📈 Статистика", callback_data="show_stats"),
        ],
        [
            types.InlineKeyboardButton(text="🔄 Сбросить активное кормление", callback_data="reset_active_feeding")
        ]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_feeding_control_keyboard() -> types.InlineKeyboardMarkup:
    """Клавиатура управления кормлением"""
    keyboard = [
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
        [
            types.InlineKeyboardButton(text="📝 Ввести своё количество", callback_data="add_custom")
        ],
        [
            types.InlineKeyboardButton(text="✅ Завершить", callback_data="finish_feeding"),
            types.InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_feeding")
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
            types.InlineKeyboardButton(text="🌞 Бодрствование", callback_data="wake_menu")
        ],
        [
            types.InlineKeyboardButton(text="🔙 В главное меню", callback_data="main_menu")
        ]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

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

def get_cancel_keyboard() -> types.InlineKeyboardMarkup:
    """Клавиатура с кнопкой отмены"""
    keyboard = [
        [
            types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_state")
        ]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

# --- Обработчики ---
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

@router.callback_query(F.data == "reset_active_feeding")
async def reset_active_feeding_callback(callback: CallbackQuery):
    """Сброс активного кормления (защита от багов)"""
    chat_id = callback.message.chat.id
    deleted_count = db.delete_active_feeding(chat_id)
    
    if deleted_count > 0:
        await callback.answer(f"✅ Удалено {deleted_count} активных кормлений", show_alert=True)
    else:
        await callback.answer("⚠️ Активных кормлений не найдено", show_alert=True)
    
    await main_menu_callback(callback)

@router.callback_query(F.data == "cancel_state")
async def cancel_state_callback(callback: CallbackQuery, state: FSMContext):
    """Отмена текущего состояния"""
    await state.clear()
    await callback.message.edit_text(
        "❌ Ввод отменен",
        reply_markup=get_main_menu_keyboard()
    )
    await callback.answer("Ввод отменен")

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
        f"📅 Дата: {get_moscow_time().strftime('%d.%m.%Y')}\n\n"
        "Выберите действие:",
        reply_markup=get_sleep_menu_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "start_sleep")
async def start_sleep_callback(callback: CallbackQuery):
    """Начало сна"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка", show_alert=True)
        return
    
    active_sleep = db.get_active_sleep(child['id'])
    if active_sleep:
        await callback.answer("Уже есть активный сон! Сначала завершите его.", show_alert=True)
        return
    
    active_wake = db.get_active_wakefulness(child['id'])
    if active_wake:
        db.end_wakefulness(active_wake['id'])
    
    sleep_id = db.start_sleep(child['id'])
    
    current_time = get_moscow_time().strftime("%H:%M")
    await callback.message.edit_text(
        f"🛏️ Сон начат в {current_time}\n"
        f"👶 Для: {child['first_name']}\n\n"
        "Когда ребенок проснется, нажмите '🌅 Конец сна'",
        reply_markup=get_sleep_menu_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "end_sleep")
async def end_sleep_callback(callback: CallbackQuery):
    """Конец сна"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка", show_alert=True)
        return
    
    active_sleep = db.get_active_sleep(child['id'])
    if not active_sleep:
        await callback.answer("Нет активного сна!", show_alert=True)
        return
    
    db.end_sleep(active_sleep['id'])
    
    sleep_start = datetime.fromisoformat(active_sleep['sleep_start'])
    sleep_end = get_moscow_time()
    duration = int((sleep_end - sleep_start).total_seconds() / 60)
    
    hours = duration // 60
    minutes = duration % 60
    
    await callback.message.edit_text(
        f"🌅 Сон завершен!\n"
        f"👶 Для: {child['first_name']}\n"
        f"🛏️ Начало: {sleep_start.strftime('%H:%M')}\n"
        f"🌅 Конец: {sleep_end.strftime('%H:%M')}\n"
        f"⏱️ Длительность: {hours}ч {minutes}мин\n\n"
        f"✅ Отлично!",
        reply_markup=get_sleep_menu_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "sleep_stats")
async def sleep_stats_callback(callback: CallbackQuery):
    """Статистика сна"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка", show_alert=True)
        return
    
    stats = db.get_sleep_stats_today(child['id'])
    
    if stats and stats['sleep_count'] > 0:
        total_hours = stats['total_minutes'] // 60
        total_minutes = stats['total_minutes'] % 60
        avg_hours = stats['avg_minutes'] // 60
        avg_minutes = stats['avg_minutes'] % 60
        
        text = f"📊 Статистика сна за сегодня:\n\n"
        text += f"👶 Ребенок: {child['first_name']}\n"
        text += f"📅 Дата: {get_moscow_time().strftime('%d.%m.%Y')}\n"
        text += f"🛏️ Количество снов: {stats['sleep_count']}\n"
        text += f"⏱️ Общее время сна: {total_hours}ч {total_minutes}мин\n"
        text += f"📈 Средняя длительность: {avg_hours}ч {avg_minutes}мин\n\n"
    else:
        text = "📊 Статистика сна за сегодня:\n\n😴 Данных о сне за сегодня пока нет"
    
    await callback.message.edit_text(
        text,
        reply_markup=get_sleep_menu_keyboard()
    )
    await callback.answer()

# --- Обработчики бодрствования ---
@router.callback_query(F.data == "wake_menu")
async def wake_menu_callback(callback: CallbackQuery):
    """Меню бодрствования"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка", show_alert=True)
        return
    
    await callback.message.edit_text(
        f"🌞 Отслеживание бодрствования\n\n"
        f"👶 Ребенок: {child['first_name']}\n"
        f"📅 Дата: {get_moscow_time().strftime('%d.%m.%Y')}\n\n"
        "Выберите действие:",
        reply_markup=get_wake_menu_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "start_wake")
async def start_wake_callback(callback: CallbackQuery):
    """Начало бодрствования"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка", show_alert=True)
        return
    
    active_wake = db.get_active_wakefulness(child['id'])
    if active_wake:
        await callback.answer("Уже есть активное бодрствование!", show_alert=True)
        return
    
    active_sleep = db.get_active_sleep(child['id'])
    if active_sleep:
        db.end_sleep(active_sleep['id'])
    
    wake_id = db.start_wakefulness(child['id'])
    
    current_time = get_moscow_time().strftime("%H:%M")
    await callback.message.edit_text(
        f"🌞 Бодрствование начато в {current_time}\n"
        f"👶 Для: {child['first_name']}\n\n"
        "Когда ребенок начнет засыпать, нажмите '🌜 Конец бодрствования'",
        reply_markup=get_wake_menu_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "end_wake")
async def end_wake_callback(callback: CallbackQuery):
    """Конец бодрствования"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка", show_alert=True)
        return
    
    active_wake = db.get_active_wakefulness(child['id'])
    if not active_wake:
        await callback.answer("Нет активного бодрствования!", show_alert=True)
        return
    
    db.end_wakefulness(active_wake['id'])
    
    wake_start = datetime.fromisoformat(active_wake['wake_start'])
    wake_end = get_moscow_time()
    duration = int((wake_end - wake_start).total_seconds() / 60)
    
    hours = duration // 60
    minutes = duration % 60
    
    await callback.message.edit_text(
        f"🌜 Бодрствование завершено!\n"
        f"👶 Для: {child['first_name']}\n"
        f"🌞 Начало: {wake_start.strftime('%H:%M')}\n"
        f"🌜 Конец: {wake_end.strftime('%H:%M')}\n"
        f"⏱️ Длительность: {hours}ч {minutes}мин",
        reply_markup=get_wake_menu_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "wake_stats")
async def wake_stats_callback(callback: CallbackQuery):
    """Статистика бодрствования"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка", show_alert=True)
        return
    
    stats = db.get_wakefulness_stats_today(child['id'])
    
    if stats and stats['wake_count'] > 0:
        total_hours = stats['total_minutes'] // 60
        total_minutes = stats['total_minutes'] % 60
        avg_hours = stats['avg_minutes'] // 60
        avg_minutes = stats['avg_minutes'] % 60
        
        text = f"📊 Статистика бодрствования за сегодня:\n\n"
        text += f"👶 Ребенок: {child['first_name']}\n"
        text += f"📅 Дата: {get_moscow_time().strftime('%d.%m.%Y')}\n"
        text += f"🌞 Количество периодов: {stats['wake_count']}\n"
        text += f"⏱️ Общее время: {total_hours}ч {total_minutes}мин\n"
        text += f"📈 Средняя длительность: {avg_hours}ч {avg_minutes}мин"
    else:
        text = "📊 Статистика бодрствования за сегодня:\n\n🌞 Данных о бодрствовании за сегодня пока нет"
    
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
        await callback.answer("Сначала зарегистрируйте ребенка", show_alert=True)
        return
    
    await callback.message.edit_text(
        f"🩲 Отслеживание подгузников\n\n"
        f"👶 Ребенок: {child['first_name']}\n"
        f"📅 Дата: {get_moscow_time().strftime('%d.%m.%Y')}\n\n"
        "Выберите тип:",
        reply_markup=get_diaper_menu_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data.in_(["diaper_urine", "diaper_poop", "diaper_both"]))
async def process_diaper_callback(callback: CallbackQuery):
    """Обработка подгузников"""
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
    
    current_time = get_moscow_time().strftime("%H:%M")
    
    text = f"✅ Подгузник отмечен!\n\n"
    text += f"👶 Ребенок: {child['first_name']}\n"
    text += f"📅 Дата: {get_moscow_time().strftime('%d.%m.%Y')}\n"
    text += f"⏰ Время: {current_time}\n"
    text += f"🩲 Тип: {diaper_type}\n\n"
    
    await callback.message.edit_text(
        text,
        reply_markup=get_diaper_menu_keyboard()
    )
    await callback.answer("✅ Запись сохранена!")

@router.callback_query(F.data == "diaper_stats")
async def diaper_stats_callback(callback: CallbackQuery):
    """Статистика подгузников"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка", show_alert=True)
        return
    
    stats = db.get_diaper_stats_today(child['id'])
    
    text = f"📊 Статистика подгузников за сегодня:\n\n"
    text += f"👶 Ребенок: {child['first_name']}\n"
    text += f"📅 Дата: {get_moscow_time().strftime('%d.%m.%Y')}\n\n"
    
    if stats:
        for row in stats:
            emoji = {"мочеиспускание": "💦", "стул": "💩", "оба": "💦💩"}.get(row['type'], "🩲")
            text += f"{emoji} {row['type'].title()}: {row['count']} раз\n"
    else:
        text += "🩲 Данных за сегодня пока нет"
    
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
        await callback.answer("Сначала зарегистрируйте ребенка", show_alert=True)
        return
    
    await callback.message.edit_text(
        f"📝 Журнал заметок\n\n"
        f"👶 Ребенок: {child['first_name']}\n"
        f"📅 Дата: {get_moscow_time().strftime('%d.%m.%Y')}\n\n"
        "Введите заметку (температура, настроение, особенности поведения, питание и т.д.):\n\n"
        "Для отмены нажмите ❌ Отмена",
        reply_markup=get_cancel_keyboard()
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

# --- Команды бота ---
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

Функции для родителей:
• 💤 Сон - Трекер сна
• 🌞 Бодрствование - Трекер времени бодрствования
• 🩲 Подгузник - Трекер смены подгузников
• 📝 Заметка - Журнал для записей

Для кормлений:
/feeding - Начать кормление
/add_eaten [количество] - Добавить съеденное (например: /add_eaten 50)
/finish - Завершить кормление
/reset_feeding - Сбросить активное кормление (при багах)

Для отмены ввода:
/cancel - Отмена текущего действия"""
    
    await message.answer(help_text)

# --- Обработчики команд кормления ---
@router.message(Command("feeding"))
async def feeding_cmd(message: Message):
    """Команда для начала кормления"""
    chat_id = message.chat.id
    child = db.get_child(chat_id)
    
    if not child:
        await message.answer("Сначала зарегистрируйте ребенка с помощью /register")
        return
    
    active_feeding = db.get_active_feeding(chat_id)
    if active_feeding:
        await message.answer("Уже есть активное кормление!")
        return
    
    feeding_id = db.start_feeding(chat_id, child['id'])
    
    daily_stats = db.get_daily_feeding_stats(child['id'])
    daily_count = daily_stats['feedings_count'] if daily_stats else 0
    daily_total = daily_stats['total_ml'] if daily_stats else 0
    
    text = (
        f"🍼 Кормление начато!\n\n"
        f"👶 Ребенок: {child['first_name']}\n"
        f"⏱️ Начало: {get_moscow_time().strftime('%H:%M')}\n"
        f"🍶 Съедено сейчас: 0 мл\n"
        f"📊 За сегодня: {daily_count} кормлений, всего {daily_total} мл\n\n"
        "Добавляйте съеденное по мере кормления:"
    )
    
    await message.answer(text, reply_markup=get_feeding_control_keyboard())

@router.message(Command("add_eaten"))
async def add_eaten_cmd(message: Message):
    """Команда для добавления съеденного количества"""
    chat_id = message.chat.id
    feeding = db.get_active_feeding(chat_id)
    
    if not feeding:
        await message.answer("Нет активного кормления!")
        return
    
    try:
        args = message.text.split()
        if len(args) < 2:
            await message.answer("Использование: /add_eaten [количество в мл]\nНапример: /add_eaten 50")
            return
        
        eaten_ml = int(args[1])
        if eaten_ml <= 0 or eaten_ml > 500:
            await message.answer("Введите количество от 1 до 500 мл!")
            return
        
        db.add_eaten_ml(feeding['id'], eaten_ml)
        
        child = db.get_child(chat_id)
        total_eaten = (feeding['total_eaten_ml'] or 0) + eaten_ml
        
        daily_stats = db.get_daily_feeding_stats(child['id'])
        daily_count = daily_stats['feedings_count'] if daily_stats else 0
        daily_total = daily_stats['total_ml'] if daily_stats else 0
        
        text = (
            f"✅ Добавлено {eaten_ml} мл\n\n"
            f"👶 Ребенок: {child['first_name']}\n"
            f"🍶 Съедено сейчас: {total_eaten} мл\n"
            f"📊 За сегодня: {daily_count} кормлений, всего {daily_total} мл"
        )
        
        await message.answer(text)
        
    except ValueError:
        await message.answer("Введите число (например: /add_eaten 50)")

@router.message(Command("finish"))
async def finish_cmd(message: Message):
    """Команда для завершения кормления"""
    chat_id = message.chat.id
    feeding = db.get_active_feeding(chat_id)
    
    if not feeding:
        await message.answer("Нет активного кормления!")
        return
    
    db.finish_feeding(feeding['id'])
    
    child = db.get_child(chat_id)
    start_time = datetime.fromisoformat(feeding['start_time'])
    end_time = get_moscow_time()
    duration = end_time - start_time
    
    total_duration_seconds = int(duration.total_seconds()) - (feeding['total_pause_duration'] or 0)
    
    daily_stats = db.get_daily_feeding_stats(child['id'])
    daily_count = daily_stats['feedings_count'] if daily_stats else 0
    daily_total = daily_stats['total_ml'] if daily_stats else 0

    today_feedings = db.get_today_feedings(child['id'])
    
    text = (
        f"✅ Кормление завершено!\n\n"
        f"👶 Ребенок: {child['first_name']}\n"
        f"⏱️ Начало: {start_time.strftime('%H:%M')}\n"
        f"⏱️ Конец: {end_time.strftime('%H:%M')}\n"
        f"⏳ Длительность: {format_duration(total_duration_seconds)}\n"
        f"🍶 Съедено: {feeding['total_eaten_ml'] or 0} мл\n"
        f"📊 За сегодня: {daily_count} кормлений, всего {daily_total} мл"
    )
    
    if today_feedings:
        text += "\n\n📋 Кормления за сегодня:\n"
        for f in today_feedings:
            text += f"  {f['start_time']} - {f['end_time']}: {f['total_eaten_ml']} мл\n"
    
    if feeding['prepared_ml']:
        text += f"\n🍶 Приготовлено: {feeding['prepared_ml']} мл"
    
    await message.answer(text)
    await message.answer("🏠 Главное меню\nВыберите раздел:", reply_markup=get_main_menu_keyboard())

@router.message(Command("reset_feeding"))
async def reset_feeding_cmd(message: Message):
    """Команда для сброса активного кормления"""
    chat_id = message.chat.id
    deleted_count = db.delete_active_feeding(chat_id)
    
    if deleted_count > 0:
        await message.answer(f"✅ Удалено {deleted_count} активных кормлений")
    else:
        await message.answer("⚠️ Активных кормлений не найдено")

@router.message(Command("cancel"))
async def cancel_cmd(message: Message, state: FSMContext):
    """Команда для отмены текущего действия"""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Нет активных действий для отмены")
        return
    
    await state.clear()
    await message.answer(
        "❌ Действие отменено",
        reply_markup=get_main_menu_keyboard()
    )

# --- Обработчики кормления через callback ---
@router.callback_query(F.data == "start_feeding")
async def start_feeding_callback(callback: CallbackQuery):
    """Начало кормления через callback"""
    chat_id = callback.message.chat.id
    child = db.get_child(chat_id)
    
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка", show_alert=True)
        return
    
    active_feeding = db.get_active_feeding(chat_id)
    if active_feeding:
        await callback.answer("Уже есть активное кормление!", show_alert=True)
        return
    
    feeding_id = db.start_feeding(chat_id, child['id'])
    
    daily_stats = db.get_daily_feeding_stats(child['id'])
    daily_count = daily_stats['feedings_count'] if daily_stats else 0
    daily_total = daily_stats['total_ml'] if daily_stats else 0
    
    text = (
        f"🍼 Кормление начато!\n\n"
        f"👶 Ребенок: {child['first_name']}\n"
        f"⏱️ Начало: {get_moscow_time().strftime('%H:%M')}\n"
        f"🍶 Съедено сейчас: 0 мл\n"
        f"📊 За сегодня: {daily_count} кормлений, всего {daily_total} мл\n\n"
        "Добавляйте съеденное по мере кормления:"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=get_feeding_control_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "finish_feeding")
async def finish_feeding_callback(callback: CallbackQuery):
    """Завершение кормления через callback"""
    chat_id = callback.message.chat.id
    feeding = db.get_active_feeding(chat_id)
    
    if not feeding:
        await callback.answer("Нет активного кормления!", show_alert=True)
        return
    
    db.finish_feeding(feeding['id'])
    
    child = db.get_child(chat_id)
    start_time = datetime.fromisoformat(feeding['start_time'])
    end_time = get_moscow_time()
    duration = end_time - start_time
    
    total_duration_seconds = int(duration.total_seconds()) - (feeding['total_pause_duration'] or 0)
    
    daily_stats = db.get_daily_feeding_stats(child['id'])
    daily_count = daily_stats['feedings_count'] if daily_stats else 0
    daily_total = daily_stats['total_ml'] if daily_stats else 0

    today_feedings = db.get_today_feedings(child['id'])
    
    text = (
        f"✅ Кормление завершено!\n\n"
        f"👶 Ребенок: {child['first_name']}\n"
        f"⏱️ Начало: {start_time.strftime('%H:%M')}\n"
        f"⏱️ Конец: {end_time.strftime('%H:%M')}\n"
        f"⏳ Длительность: {format_duration(total_duration_seconds)}\n"
        f"🍶 Съедено: {feeding['total_eaten_ml'] or 0} мл\n"
        f"📊 За сегодня: {daily_count} кормлений, всего {daily_total} мл"
    )
    
    if today_feedings:
        text += "\n\n📋 Кормления за сегодня:\n"
        for f in today_feedings:
            text += f"  {f['start_time']} - {f['end_time']}: {f['total_eaten_ml']} мл\n"
    
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
    """Отмена кормления через callback"""
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

# --- Обработчики быстрого добавления еды ---
@router.callback_query(F.data.in_(["add_5", "add_10", "add_20", "add_30", "add_50", "add_100"]))
async def add_eaten_quick_callback(callback: CallbackQuery):
    """Быстрое добавление съеденного"""
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
    
    daily_stats = db.get_daily_feeding_stats(child['id'])
    daily_count = daily_stats['feedings_count'] if daily_stats else 0
    daily_total = daily_stats['total_ml'] if daily_stats else 0
    
    text = (
        f"🍼 Кормление продолжается\n\n"
        f"👶 Ребенок: {child['first_name']}\n"
        f"⏱️ Начало: {datetime.fromisoformat(feeding['start_time']).strftime('%H:%M')}\n"
        f"🍶 Съедено сейчас: {total_eaten} мл\n"
        f"📊 За сегодня: {daily_count} кормлений, всего {daily_total} мл\n\n"
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
        "Введите число (например: 75):\n\n"
        "Для отмены нажмите ❌ Отмена",
        reply_markup=get_cancel_keyboard()
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
        
        if eaten_ml > 500:
            await message.answer("Введите количество до 500 мл!")
            return
        
        db.add_eaten_ml(feeding['id'], eaten_ml)
        
        child = db.get_child(chat_id)
        if not child:
            await message.answer("Ребенок не найден!")
            await state.clear()
            return
            
        total_eaten = (feeding['total_eaten_ml'] or 0) + eaten_ml
        
        daily_stats = db.get_daily_feeding_stats(child['id'])
        daily_count = daily_stats['feedings_count'] if daily_stats else 0
        daily_total = daily_stats['total_ml'] if daily_stats else 0
        
        text = (
            f"🍼 Кормление продолжается\n\n"
            f"👶 Ребенок: {child['first_name']}\n"
            f"⏱️ Начало: {datetime.fromisoformat(feeding['start_time']).strftime('%H:%M')}\n"
            f"🍶 Съедено сейчас: {total_eaten} мл\n"
            f"📊 За сегодня: {daily_count} кормлений, всего {daily_total} мл\n\n"
            f"✅ Добавлено: {eaten_ml} мл\n\n"
            "Продолжайте кормить или завершите кормление"
        )
        
        await message.answer(text, reply_markup=get_feeding_control_keyboard())
        await state.clear()
        
    except ValueError:
        await message.answer("Пожалуйста, введите число (например: 75):")

# --- Обработчики параметров ---
@router.callback_query(F.data == "update_params")
async def update_params_callback(callback: CallbackQuery, state: FSMContext):
    """Обновление параметров через callback"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка!", show_alert=True)
        return
    
    await callback.message.edit_text(
        f"📊 Внесение параметров\n\n"
        f"👶 Ребенок: {child['first_name']}\n\n"
        "Введите текущий вес ребенка в граммах (например: 4500):\n\n"
        "Для отмены нажмите ❌ Отмена",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(UpdateParams.waiting_for_weight)
    await callback.answer()

@router.message(UpdateParams.waiting_for_weight)
async def process_weight(message: Message, state: FSMContext):
    try:
        weight = float(message.text)
        if 500 <= weight <= 20000:
            await state.update_data(weight=weight)
            await message.answer(
                "Введите текущий рост в см (например: 60):\n\n"
                "Для отмены нажмите ❌ Отмена",
                reply_markup=get_cancel_keyboard()
            )
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

# --- Обработчики статистики ---
@router.callback_query(F.data == "show_stats")
async def show_stats_callback(callback: CallbackQuery):
    """Показать статистику через callback"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Сначала зарегистрируйте ребенка!", show_alert=True)
        return
    
    await show_stats_dialog(callback.message)
    await callback.answer()

async def show_stats_dialog(message: Message):
    child = db.get_child(message.chat.id)
    if not child:
        await message.answer("Сначала зарегистрируйте ребенка")
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
    
    # Детальные кормления за сегодня
    today_feedings = db.get_today_feedings(child['id'])
    daily_stats = db.get_daily_feeding_stats(child['id'])
    if today_feedings:
        text += "🍼 Кормления сегодня:\n"
        for f in today_feedings:
            text += f"  {f['start_time']} - {f['end_time']}: {f['total_eaten_ml']} мл\n"
        text += f"  Всего за сегодня: {daily_stats['total_ml']} мл ({daily_stats['feedings_count']} корм.)\n\n"
    else:
        text += "🍼 Сегодня кормлений не было.\n\n"
    
    if feedings_stats:
        text += "🍼 Кормления за последние 7 дней:\n"
        for stat in feedings_stats:
            text += f"  📅 {stat['feeding_date']}: {stat['feedings_count']} кормлений, {stat['total_ml'] or 0} мл\n"
        text += "\n"
    
    if measurements:
        text += "📈 Динамика параметров:\n"
        for i, m in enumerate(measurements):
            recorded_time = ""
            if m['recorded_at']:
                try:
                    if isinstance(m['recorded_at'], str):
                        dt = datetime.fromisoformat(m['recorded_at'])
                        recorded_time = f" ({dt.strftime('%H:%M')})"
                except:
                    pass
            
            if i == 0:
                text += f"  📅 {m['measurement_date']}{recorded_time}: {m['weight']} г, {m['height']} см (последнее)\n"
            else:
                text += f"  📅 {m['measurement_date']}{recorded_time}: {m['weight']} г, {m['height']} см\n"
    else:
        text += "📏 Нет данных об измерениях\n"
    
    # Статистика сна, бодрствования, подгузников
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
            emoji = {"мочеиспускание": "💦", "стул": "💩", "оба": "💦💩"}.get(row['type'], "🩲")
            text += f"{emoji}{row['count']} "
    
    await message.answer(text)
    await message.answer("🏠 Главное меню\nВыберите раздел:", reply_markup=get_main_menu_keyboard())

# --- Обработчики информации о ребенке ---
@router.callback_query(F.data == "child_info")
async def child_info_callback(callback: CallbackQuery):
    """Информация о ребенке"""
    child = db.get_child(callback.message.chat.id)
    if not child:
        await callback.answer("Ребенок не зарегистрирован", show_alert=True)
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

# --- Обработчики команды /register ---
@router.message(Command("register"))
async def register_child_cmd(message: Message, state: FSMContext):
    child = db.get_child(message.chat.id)
    if child:
        await message.answer("Ребенок уже зарегистрирован! Используйте /child_info для просмотра данных.")
        return
    await message.answer(
        "Введите имя ребенка:\n\n"
        "Для отмены нажмите ❌ Отмена",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(ChildRegistration.waiting_for_first_name)

@router.message(ChildRegistration.waiting_for_first_name)
async def process_first_name(message: Message, state: FSMContext):
    await state.update_data(first_name=message.text)
    await message.answer(
        "Введите фамилию ребенка (или напишите '-' если нет):\n\n"
        "Для отмены нажмите ❌ Отмена",
        reply_markup=get_cancel_keyboard()
    )
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
    await callback.message.answer(
        "Введите дату рождения в формате ДД.ММ.ГГГГ:\n\n"
        "Для отмены нажмите ❌ Отмена",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(ChildRegistration.waiting_for_birth_date)
    await callback.answer()

@router.message(ChildRegistration.waiting_for_birth_date)
async def process_birth_date(message: Message, state: FSMContext):
    try:
        birth_date = datetime.strptime(message.text, "%d.%m.%Y")
        await state.update_data(birth_date=birth_date.strftime("%Y-%m-%d"))
        await message.answer(
            "Введите срок беременности (недели от 20 до 42):\n\n"
            "Для отмены нажмите ❌ Отмена",
            reply_markup=get_cancel_keyboard()
        )
        await state.set_state(ChildRegistration.waiting_for_gestation_weeks)
    except ValueError:
        await message.answer("Неверный формат даты. Введите в формате ДД.ММ.ГГГГ:")

@router.message(ChildRegistration.waiting_for_gestation_weeks)
async def process_gestation_weeks(message: Message, state: FSMContext):
    try:
        weeks = int(message.text)
        if 20 <= weeks <= 42:
            await state.update_data(gestation_weeks=weeks)
            await message.answer(
                "Введите дополнительные дни срока (0-6):\n\n"
                "Для отмены нажмите ❌ Отмена",
                reply_markup=get_cancel_keyboard()
            )
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
            await message.answer(
                "Введите вес при рождении (в граммах, например: 3500):\n\n"
                "Для отмены нажмите ❌ Отмена",
                reply_markup=get_cancel_keyboard()
            )
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
            await message.answer(
                "Введите рост при рождении (в см, например: 52):\n\n"
                "Для отмены нажмите ❌ Отмена",
                reply_markup=get_cancel_keyboard()
            )
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
    
    await message.answer(
        "Введите текущий вес ребенка в граммах (например: 4500):\n\n"
        "Для отмены нажмите ❌ Отмена",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(UpdateParams.waiting_for_weight)

@router.message(Command("stats"))
async def stats_cmd(message: Message):
    await show_stats_dialog(message)

# --- Заглушка для неиспользуемых callback-данных ---
@router.callback_query(F.data.in_([
    "temp_tracking", "vaccination_info", "doctor_visit", "medical_record",
    "general_stats", "feeding_stats", "weight_chart", "height_chart", 
    "monthly_report", "daily_report", "sleep_history"
]))
async def placeholder_callback(callback: CallbackQuery):
    """Заглушка для пока не реализованных функций"""
    await callback.answer("Эта функция скоро будет доступна! ⏳", show_alert=True)

# --- Система напоминаний ---
async def check_reminders():
    while True:
        try:
            reminders = db.get_reminders_due()
            for reminder in reminders:
                child = db.get_child(reminder['chat_id'])
                if child:
                    birth_date = datetime.strptime(child['birth_date'], "%Y-%m-%d")
                    age_days = (get_moscow_time().date() - birth_date.date()).days
                    
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
