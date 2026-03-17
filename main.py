import kivy
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.progressbar import ProgressBar
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.navigationdrawer import NavigationDrawer
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle
import requests
import json
import time
import random
import asyncio
import os
import threading
from bleak import BleakScanner, BleakClient, BleakError
from aip import AipImageClassify
from plyer import camera
import json
import os

# -------------------------- 已为你填好百度API密钥，直接用即可 --------------------------
BAIDU_APP_ID = "75237339"
BAIDU_API_KEY = "QADSHdIHCX2bhnNiqPYNXAIH"
BAIDU_SECRET_KEY = "JLIrIP6wFfahPHAgmZ1FU5ezSuT3yx4d"
# ------------------------------------------------------------------------------------------

# 其他配置（不用改）
USDA_API_KEY = "YOUR_USDA_API_KEY"  # 备用配置，不影响使用
USDA_API_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
BLE_UUID_HEART_RATE = "00002a37-0000-1000-8000-00805f9b34fb"
BLE_UUID_STEP_COUNT = "0000ff06-0000-1000-8000-00805f9b34fb"
BLE_UUID_BLOOD_PRESSURE = "00002a35-0000-1000-8000-00805f9b34fb"
BLE_UUID_CUSTOM_DISPLAY = "0000ff09-0000-1000-8000-00805f9b34fb"

loop = asyncio.get_event_loop()
baidu_client = AipImageClassify(BAIDU_APP_ID, BAIDU_API_KEY, BAIDU_SECRET_KEY)

# 临时文件路径（适配打包环境）
TEMP_IMAGE_PATH = "food_photo.jpg"
FOOD_HISTORY_PATH = "food_history.json"


# -------------------------- 核心身体数据类 --------------------------
class BodyStats:
    def __init__(self, name, height, weight, age, gender, resting_heart_rate=70, goal="maintain"):
        self.name = name
        self.height = height
        self.weight = weight
        self.age = age
        self.gender = gender
        self.resting_heart_rate = resting_heart_rate
        self.goal = goal

        self.realtime_heart_rate = resting_heart_rate
        self.daily_steps = 0
        self.systolic_bp = 120
        self.diastolic_bp = 80
        self.current_activity = "静坐"
        self.sleep_hours = 7.0
        self.deep_sleep_ratio = 0.3

        self.daily_calories_burned = 0
        self.daily_calories_intake = 0
        self.daily_protein_intake = 0
        self.daily_carbs_intake = 0
        self.daily_fat_intake = 0

        self.history = []
        self.food_history = []
        self.load_food_history()
        self.update_body_stats()
        self.save_history()

    def calculate_bmi(self):
        return round(self.weight / ((self.height / 100) ** 2), 1)

    def calculate_body_fat(self):
        bmi = self.calculate_bmi()
        if self.gender == 'male':
            fat = 1.20 * bmi + 0.23 * self.age - 16.2
        else:
            fat = 1.20 * bmi + 0.23 * self.age - 5.4
        return max(5, min(50, round(fat, 1)))

    def calculate_muscle_mass(self):
        body_fat = self.calculate_body_fat()
        muscle = self.weight * (1 - body_fat / 100) - 2.5
        return max(20, round(muscle, 1))

    def calculate_strength(self):
        muscle_ratio = self.muscle_mass / self.weight
        if self.gender == 'male':
            base_strength = min(100, max(0, int((muscle_ratio - 0.25) / 0.15 * 100)))
        else:
            base_strength = min(100, max(0, int((muscle_ratio - 0.20) / 0.15 * 100)))
        return base_strength

    def calculate_stamina(self):
        hr_score = max(0, 50 - abs(self.resting_heart_rate - 55) * 1.5)
        body_fat = self.body_fat
        bmi = self.bmi
        if self.gender == 'male':
            fat_score = 50 if 10 <= body_fat <= 15 else max(0, 50 - abs(body_fat - 12.5) * 2)
        else:
            fat_score = 50 if 20 <= body_fat <= 25 else max(0, 50 - abs(body_fat - 22.5) * 2)
        bmi_score = 50 if 18.5 <= bmi <= 24.9 else max(0, 50 - abs(bmi - 21.7) * 3)
        total_score = int((hr_score + fat_score + bmi_score) / 1.5)
        return min(100, max(0, total_score))

    def calculate_bmr(self):
        if self.gender == 'male':
            return 10 * self.weight + 6.25 * self.height - 5 * self.age + 5
        else:
            return 10 * self.weight + 6.25 * self.height - 5 * self.age - 161

    def calculate_activity_calories(self, duration_minutes, heart_rate):
        bmr = self.calculate_bmr()
        if self.gender == 'male':
            calories = ((-55.0969 + (0.6309 * heart_rate) + (0.1988 * self.weight) + (
                        0.2017 * self.age)) / 4.184) * duration_minutes
        else:
            calories = ((-20.4022 + (0.4472 * heart_rate) - (0.1263 * self.weight) + (
                        0.074 * self.age)) / 4.184) * duration_minutes
        return max(0, round(calories, 1))

    def recognize_activity(self, heart_rate, steps_delta):
        if steps_delta == 0 and heart_rate < 75:
            return "静坐"
        elif steps_delta > 10 and 75 <= heart_rate < 100:
            return "散步"
        elif steps_delta > 30 and 100 <= heart_rate < 140:
            return "快走/慢跑"
        elif heart_rate >= 140 and steps_delta > 20:
            return "高强度有氧"
        elif heart_rate >= 120 and steps_delta < 5:
            return "力量训练"
        elif heart_rate < 60 and steps_delta == 0:
            return "睡眠"
        else:
            return "未知活动"

    def auto_update_from_band(self, heart_rate, total_steps, systolic_bp, diastolic_bp, duration_minutes=15):
        self.realtime_heart_rate = heart_rate
        self.systolic_bp = systolic_bp
        self.diastolic_bp = diastolic_bp
        steps_delta = total_steps - self.daily_steps
        self.daily_steps = total_steps

        self.current_activity = self.recognize_activity(heart_rate, steps_delta)
        activity_calories = self.calculate_activity_calories(duration_minutes, heart_rate)
        self.daily_calories_burned += activity_calories

        daily_bmr = self.calculate_bmr()
        total_daily_expenditure = daily_bmr + self.daily_calories_burned
        calorie_balance = self.daily_calories_intake - total_daily_expenditure

        if calorie_balance < 0:
            fat_loss = round(abs(calorie_balance) / 7700, 3)
            self.weight = max(40, self.weight - fat_loss)
            muscle_loss = 0 if self.daily_protein_intake >= self.weight * 1.6 else round(fat_loss * 0.3, 1)
            self.muscle_mass = max(20, self.muscle_mass - muscle_loss)
        else:
            weight_gain = round(calorie_balance / 7700, 3)
            self.weight += weight_gain
            if self.daily_protein_intake >= self.weight * 1.8 and "力量训练" in self.current_activity:
                muscle_gain = round(weight_gain * 0.6, 1)
                self.muscle_mass += muscle_gain

        if self.current_activity == "睡眠" and heart_rate < self.resting_heart_rate:
            self.resting_heart_rate = heart_rate

        self.update_body_stats()
        return f"【{self.current_activity}】消耗{activity_calories}大卡"

    def update_sleep_data(self, total_hours, deep_sleep_hours):
        self.sleep_hours = total_hours
        self.deep_sleep_ratio = deep_sleep_hours / total_hours if total_hours > 0 else 0
        self.update_body_stats()

    def eat(self, food_name, total_calories, protein, carbs, fat):
        weight_gain = round(total_calories / 7700, 2)
        muscle_from_protein = round(protein * 0.03 + random.uniform(-0.1, 0.1), 1)

        self.weight += weight_gain
        self.muscle_mass += muscle_from_protein
        self.daily_calories_intake += total_calories
        self.daily_protein_intake += protein
        self.daily_carbs_intake += carbs
        self.daily_fat_intake += fat

        self.food_history.append({
            'time': time.strftime("%Y-%m-%d %H:%M"),
            'food_name': food_name,
            'calories': total_calories,
            'protein': protein,
            'carbs': carbs,
            'fat': fat
        })
        self.save_food_history()

        self.update_body_stats()
        self.save_history()
        return f"✅ 已记录【{food_name}】| 摄入{total_calories}大卡"

    def get_diet_recommendation(self):
        bmr = self.calculate_bmr()
        if self.goal == "lose":
            target_calories = bmr * 1.2 - 500
            target_protein = self.weight * 2.0
            target_carbs = self.weight * 2.0
            target_fat = self.weight * 0.8
        elif self.goal == "gain":
            target_calories = bmr * 1.6 + 300
            target_protein = self.weight * 2.2
            target_carbs = self.weight * 4.0
            target_fat = self.weight * 1.0
        else:
            target_calories = bmr * 1.4
            target_protein = self.weight * 1.6
            target_carbs = self.weight * 3.0
            target_fat = self.weight * 0.9

        return {
            'target_calories': round(target_calories),
            'target_protein': round(target_protein),
            'target_carbs': round(target_carbs),
            'target_fat': round(target_fat),
            'recommendation': [
                "早餐：燕麦粥+鸡蛋+牛奶",
                "午餐：糙米饭+鸡胸肉+西兰花",
                "晚餐：红薯+鱼肉+青菜",
                "加餐：香蕉+坚果" if self.goal == "gain" else "加餐：苹果"
            ]
        }

    def update_body_stats(self):
        self.bmi = self.calculate_bmi()
        self.body_fat = self.calculate_body_fat()
        self.muscle_mass = self.calculate_muscle_mass()
        self.fat_level = min(100, max(0, int((self.body_fat - 5) * 2.5)))
        self.muscle_level = min(100, max(0, int((self.muscle_mass / (self.weight * 0.6)) * 100)))
        self.strength = self.calculate_strength()
        self.stamina = self.calculate_stamina()

    def save_history(self):
        self.history.append({
            'time': time.strftime("%Y-%m-%d %H:%M"),
            'weight': self.weight,
            'body_fat': self.body_fat,
            'muscle_mass': self.muscle_mass,
            'strength': self.strength,
            'stamina': self.stamina,
            'daily_steps': self.daily_steps
        })

    def save_food_history(self):
        try:
            with open(FOOD_HISTORY_PATH, 'w', encoding='utf-8') as f:
                json.dump(self.food_history, f, ensure_ascii=False)
        except:
            pass

    def load_food_history(self):
        if os.path.exists(FOOD_HISTORY_PATH):
            try:
                with open(FOOD_HISTORY_PATH, 'r', encoding='utf-8') as f:
                    self.food_history = json.load(f)
            except:
                self.food_history = []

    def exercise(self, exercise_type, duration_minutes):
        exercise_effects = {
            'run': {'calories_burn': 10, 'muscle_gain': 0.1, 'fat_loss': 0.05},
            'gym': {'calories_burn': 8, 'muscle_gain': 0.3, 'fat_loss': 0.02},
            'swim': {'calories_burn': 12, 'muscle_gain': 0.15, 'fat_loss': 0.06},
            'cycle': {'calories_burn': 9, 'muscle_gain': 0.12, 'fat_loss': 0.04}
        }
        effect = exercise_effects.get(exercise_type, exercise_effects['run'])
        weight_loss = round(effect['calories_burn'] * duration_minutes / 7700, 2)
        muscle_gain = round(effect['muscle_gain'] * duration_minutes / 30 + random.uniform(-0.1, 0.2), 1)
        self.weight = max(40, self.weight - weight_loss)
        self.muscle_mass += muscle_gain
        self.daily_calories_burned += effect['calories_burn'] * duration_minutes
        self.update_body_stats()
        self.save_history()
        return f"消耗 {effect['calories_burn'] * duration_minutes}kcal"


# -------------------------- 蓝牙手环管理类 --------------------------
class BandManager:
    def __init__(self):
        self.client = None
        self.connected_device = None
        self.is_connected = False
        self.realtime_data = {
            'heart_rate': 70,
            'steps': 0,
            'systolic_bp': 120,
            'diastolic_bp': 80
        }

    async def scan_devices(self):
        try:
            devices = await BleakScanner.discover(timeout=5.0)
            return [d for d in devices if
                    "band" in d.name.lower() or "watch" in d.name.lower() or "mi" in d.name.lower() or "huawei" in d.name.lower()]
        except BleakError as e:
            return []

    async def connect_device(self, device_address):
        try:
            self.client = BleakClient(device_address)
            self.is_connected = await self.client.connect()
            if self.is_connected:
                self.connected_device = device_address
                await self.client.start_notify(BLE_UUID_HEART_RATE, self.heart_rate_handler)
                return True
            return False
        except Exception as e:
            return False

    async def disconnect_device(self):
        if self.client and self.is_connected:
            await self.client.disconnect()
            self.is_connected = False
            self.connected_device = None

    def heart_rate_handler(self, sender, data):
        flags = data[0]
        heart_rate = data[1] if (flags & 0x01) == 0 else int.from_bytes(data[1:3], byteorder='little')
        self.realtime_data['heart_rate'] = heart_rate

    async def read_step_data(self):
        try:
            if self.is_connected:
                data = await self.client.read_gatt_char(BLE_UUID_STEP_COUNT)
                steps = int.from_bytes(data[:4], byteorder='little')
                self.realtime_data['steps'] = steps
                return steps
        except:
            return self.realtime_data['steps']

    async def send_data_to_band_display(self, fat_level, muscle_level, strength, stamina, heart_rate):
        if not self.is_connected:
            return
        try:
            display_str = f"F{fat_level:02d}M{muscle_level:02d}S{strength:02d}T{stamina:02d}H{heart_rate:03d}"
            await self.client.write_gatt_char(BLE_UUID_CUSTOM_DISPLAY, display_str.encode('utf-8'))
        except:
            pass

    def get_realtime_data(self):
        return self.realtime_data


# -------------------------- 食物识别类 --------------------------
class FoodRecognizer:
    def __init__(self, client):
        self.client = client
        self.food_results = []

    def get_file_content(self, filePath):
        try:
            with open(filePath, 'rb') as fp:
                return fp.read()
        except:
            return None

    def recognize_multi_food(self, image_path):
        image = self.get_file_content(image_path)
        if not image:
            return None, "图片不存在"

        result = self.client.multiDishDetect(image, options={"top_num": 10, "filter_threshold": 0.5})

        if "error_code" in result:
            return None, f"识别失败：{result['error_msg']}"
        if not result.get("result"):
            return None, "未识别到菜品"

        self.food_results = []
        for dish in result["result"]:
            food_name = dish["name"]
            confidence = round(dish["probability"] * 100, 1)
            calorie_per_100g = round(float(dish["calorie"]), 1)
            estimate_weight = dish.get("weight", 150)

            nutrition = dish.get("nutrition", [])
            protein = 0.0
            carbs = 0.0
            fat = 0.0
            for n in nutrition:
                if n["name"] == "蛋白质":
                    protein = round(float(n["value"]), 1)
                elif n["name"] == "碳水化合物":
                    carbs = round(float(n["value"]), 1)
                elif n["name"] == "脂肪":
                    fat = round(float(n["value"]), 1)

            self.food_results.append({
                "food_name": food_name,
                "confidence": confidence,
                "weight": estimate_weight,
                "calorie_per_100g": calorie_per_100g,
                "total_calories": round(calorie_per_100g * estimate_weight / 100, 1),
                "protein_per_100g": protein,
                "total_protein": round(protein * estimate_weight / 100, 1),
                "carbs_per_100g": carbs,
                "total_carbs": round(carbs * estimate_weight / 100, 1),
                "fat_per_100g": fat,
                "total_fat": round(fat * estimate_weight / 100, 1),
                "selected": True
            })

        return self.food_results, None


# -------------------------- Kivy主APP界面 --------------------------
class MainScreen(Screen):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self.layout = BoxLayout(orientation='vertical', padding=10, spacing=10)
        self.build_ui()
        self.add_widget(self.layout)

    def build_ui(self):
        self.layout.add_widget(Label(text="🎮 圣安地列斯身体养成", font_size=24, size_hint_y=0.08))

        realtime_panel = GridLayout(cols=4, spacing=5, size_hint_y=0.1)
        self.hr_label = Label(text=f"❤️ 心率: --")
        self.steps_label = Label(text=f"👟 步数: --")
        self.bp_label = Label(text=f"🩸 血压: --")
        self.activity_label = Label(text=f"🏃 状态: --")
        realtime_panel.add_widget(self.hr_label)
        realtime_panel.add_widget(self.steps_label)
        realtime_panel.add_widget(self.bp_label)
        realtime_panel.add_widget(self.activity_label)
        self.layout.add_widget(realtime_panel)

        status_layout = GridLayout(cols=1, spacing=8, size_hint_y=0.5)

        fat_box = BoxLayout(orientation='horizontal', size_hint_y=0.12)
        fat_box.add_widget(Label(text="肥胖度:", size_hint_x=0.2))
        self.fat_bar = ProgressBar(max=100, value=0, size_hint_x=0.6)
        fat_box.add_widget(self.fat_bar)
        self.fat_label = Label(text="--/100", size_hint_x=0.2)
        fat_box.add_widget(self.fat_label)
        status_layout.add_widget(fat_box)

        muscle_box = BoxLayout(orientation='horizontal', size_hint_y=0.12)
        muscle_box.add_widget(Label(text="肌肉量:", size_hint_x=0.2))
        self.muscle_bar = ProgressBar(max=100, value=0, size_hint_x=0.6)
        muscle_box.add_widget(self.muscle_bar)
        self.muscle_label = Label(text="--/100", size_hint_x=0.2)
        muscle_box.add_widget(self.muscle_label)
        status_layout.add_widget(muscle_box)

        strength_box = BoxLayout(orientation='horizontal', size_hint_y=0.12)
        strength_box.add_widget(Label(text="💪 力量:", size_hint_x=0.2))
        self.strength_bar = ProgressBar(max=100, value=0, size_hint_x=0.6, color=(1, 0.5, 0, 1))
        strength_box.add_widget(self.strength_bar)
        self.strength_label = Label(text="--/100", size_hint_x=0.2)
        strength_box.add_widget(self.strength_label)
        status_layout.add_widget(strength_box)

        stamina_box = BoxLayout(orientation='horizontal', size_hint_y=0.12)
        stamina_box.add_widget(Label(text="🏃 耐力:", size_hint_x=0.2))
        self.stamina_bar = ProgressBar(max=100, value=0, size_hint_x=0.6, color=(0, 0.5, 1, 1))
        stamina_box.add_widget(self.stamina_bar)
        self.stamina_label = Label(text="--/100", size_hint_x=0.2)
        stamina_box.add_widget(self.stamina_label)
        status_layout.add_widget(stamina_box)

        self.layout.add_widget(status_layout)

        self.detail_label = Label(text="", size_hint_y=0.07)
        self.layout.add_widget(self.detail_label)

        self.layout.add_widget(Label(text="👉 向右滑动打开侧边栏", size_hint_y=0.08, color=(0.5, 0.5, 0.5, 1)))

    def update_display(self):
        if not self.app.user:
            return
        self.fat_bar.value = self.app.user.fat_level
        self.fat_label.text = f"{self.app.user.fat_level}/100"
        self.muscle_bar.value = self.app.user.muscle_level
        self.muscle_label.text = f"{self.app.user.muscle_level}/100"
        self.strength_bar.value = self.app.user.strength
        self.strength_label.text = f"{self.app.user.strength}/100"
        self.stamina_bar.value = self.app.user.stamina
        self.stamina_label.text = f"{self.app.user.stamina}/100"
        self.detail_label.text = f"BMI: {self.app.user.bmi} | 体脂: {self.app.user.body_fat}% | 肌肉: {self.app.user.muscle_mass}kg"
        self.hr_label.text = f"❤️ 心率: {self.app.user.realtime_heart_rate} 次/分"
        self.steps_label.text = f"👟 步数: {self.app.user.daily_steps} 步"
        self.bp_label.text = f"🩸 血压: {self.app.user.systolic_bp}/{self.app.user.diastolic_bp}"
        self.activity_label.text = f"🏃 状态: {self.app.user.current_activity}"


class SidebarScreen(Screen):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self.layout = BoxLayout(orientation='vertical', padding=10, spacing=10)
        self.build_ui()
        self.add_widget(self.layout)

    def build_ui(self):
        self.layout.add_widget(Label(text="📋 功能菜单", font_size=24, size_hint_y=0.08))

        buttons = [
            ("🍽️ 多菜品拍照识别", self.app.show_multi_food_camera),
            ("🥗 饮食推荐", self.app.show_diet_recommendation),
            ("📜 饮食历史", self.app.show_food_history),
            ("📱 连接手环", self.app.show_band_scan),
            ("🏃 手动锻炼", self.app.show_exercise),
            ("😴 睡眠记录", self.app.show_sleep),
            ("📈 身体历史", self.app.show_body_history),
            ("⚙️ 设置目标", self.app.show_goal_setting),
            ("👈 返回主界面", self.app.go_to_main)
        ]

        for text, callback in buttons:
            btn = Button(text=text, size_hint_y=0.08)
            btn.bind(on_press=callback)
            self.layout.add_widget(btn)


class GTABodyFitnessApp(App):
    def build(self):
        self.user = None
        self.band_manager = BandManager()
        self.food_recognizer = FoodRecognizer(baidu_client)
        self.auto_sync_event = None
        self.sm = ScreenManager()

        self.init_screen = Screen(name='init')
        self.build_init_screen()
        self.sm.add_widget(self.init_screen)

        self.main_screen = MainScreen(self, name='main')
        self.sm.add_widget(self.main_screen)

        self.sidebar_screen = SidebarScreen(self, name='sidebar')
        self.sm.add_widget(self.sidebar_screen)

        self.nav_drawer = NavigationDrawer()
        self.nav_drawer.add_widget(self.sidebar_screen)
        self.nav_drawer.attach_to(self.main_screen)

        return self.sm

    def build_init_screen(self):
        layout = BoxLayout(orientation='vertical', padding=10, spacing=10)
        layout.add_widget(Label(text="🎮 圣安地列斯现实身体养成", font_size=24, size_hint_y=0.1))

        form = GridLayout(cols=2, spacing=10, size_hint_y=0.6)
        form.add_widget(Label(text="名字:"))
        self.name_input = TextInput(multiline=False)
        form.add_widget(self.name_input)

        form.add_widget(Label(text="身高(cm):"))
        self.height_input = TextInput(multiline=False, input_filter='float')
        form.add_widget(self.height_input)

        form.add_widget(Label(text="体重(kg):"))
        self.weight_input = TextInput(multiline=False, input_filter='float')
        form.add_widget(self.weight_input)

        form.add_widget(Label(text="年龄:"))
        self.age_input = TextInput(multiline=False, input_filter='int')
        form.add_widget(self.age_input)

        form.add_widget(Label(text="性别(male/female):"))
        self.gender_input = TextInput(multiline=False)
        form.add_widget(self.gender_input)

        form.add_widget(Label(text="目标(gain/lose/maintain):"))
        self.goal_input = TextInput(multiline=False, text="maintain")
        form.add_widget(self.goal_input)

        self.init_screen.add_widget(layout)
        layout.add_widget(form)

        confirm_btn = Button(text="开启身体养成", size_hint_y=0.1, background_color=(0, 0.7, 0.3, 1))
        confirm_btn.bind(on_press=self.init_user)
        layout.add_widget(confirm_btn)

    def init_user(self, instance):
        try:
            self.user = BodyStats(
                name=self.name_input.text,
                height=float(self.height_input.text),
                weight=float(self.weight_input.text),
                age=int(self.age_input.text),
                gender=self.gender_input.text.lower(),
                goal=self.goal_input.text.lower()
            )
            self.sm.current = 'main'
            self.main_screen.update_display()
            self.toggle_auto_sync(None)
        except Exception as e:
            self.init_screen.add_widget(Label(text=f"输入错误: {str(e)}", color=(1, 0, 0, 1)))

    def go_to_main(self, instance):
        self.sm.current = 'main'

    def show_multi_food_camera(self, instance):
        self.sm.current = 'main'
        self.show_food_camera_screen()

    def show_food_camera_screen(self):
        popup = Popup(title="🍽️ 多菜品拍照识别", size_hint=(0.9, 0.9))
        layout = BoxLayout(orientation='vertical', padding=10, spacing=10)

        take_btn = Button(text="📷 点击拍照", size_hint_y=0.3, font_size=24, background_color=(0.9, 0.5, 0.3, 1))
        take_btn.bind(on_press=lambda x: self.take_multi_food_photo(popup))
        layout.add_widget(take_btn)

        close_btn = Button(text="关闭", size_hint_y=0.1, background_color=(0.7, 0.7, 0.7, 1))
        close_btn.bind(on_press=popup.dismiss)
        layout.add_widget(close_btn)

        popup.content = layout
        popup.open()

    def take_multi_food_photo(self, popup):
        try:
            camera.take_picture(filename=TEMP_IMAGE_PATH,
                                on_complete=lambda x: self.on_multi_food_photo_complete(x, popup))
        except Exception as e:
            pass

    def on_multi_food_photo_complete(self, filepath, popup):
        if not filepath or not os.path.exists(filepath):
            return

        def run_recognize(dt):
            food_results, error = self.food_recognizer.recognize_multi_food(filepath)
            if error:
                return
            self.show_multi_food_result_popup(food_results)
            popup.dismiss()

        Clock.schedule_once(run_recognize, 0.1)

    def show_multi_food_result_popup(self, food_results):
        popup = Popup(title="🍽️ 识别结果", size_hint=(0.95, 0.95))
        layout = BoxLayout(orientation='vertical', padding=10, spacing=10)

        scroll = ScrollView(size_hint=(1, 0.6))
        list_layout = GridLayout(cols=1, spacing=5, size_hint_y=None)
        list_layout.bind(minimum_height=list_layout.setter('height'))

        self.food_checkboxes = []
        for i, food in enumerate(food_results):
            item_layout = BoxLayout(orientation='horizontal', size_hint_y=None, height=60)
            item_layout.add_widget(Label(text=f"{food['food_name']}\n{food['total_calories']}大卡", size_hint_x=0.5))
            weight_input = TextInput(text=str(food['weight']), multiline=False, input_filter='float', size_hint_x=0.2)
            item_layout.add_widget(weight_input)
            food['weight_input'] = weight_input
            self.food_checkboxes.append(food)
            list_layout.add_widget(item_layout)

        scroll.add_widget(list_layout)
        layout.add_widget(scroll)

        btn_layout = BoxLayout(orientation='horizontal', spacing=10, size_hint_y=0.1)
        confirm_btn = Button(text="✅ 确认记录", background_color=(0, 0.7, 0.3, 1))
        confirm_btn.bind(on_press=lambda x: self.confirm_multi_food_record(food_results, popup))
        cancel_btn = Button(text="取消", background_color=(0.7, 0.7, 0.7, 1))
        cancel_btn.bind(on_press=popup.dismiss)
        btn_layout.add_widget(confirm_btn)
        btn_layout.add_widget(cancel_btn)
        layout.add_widget(btn_layout)

        popup.content = layout
        popup.open()

    def confirm_multi_food_record(self, food_results, popup):
        try:
            total_calories = 0
            total_protein = 0
            total_carbs = 0
            total_fat = 0
            food_names = []

            for food in food_results:
                new_weight = float(food['weight_input'].text)
                ratio = new_weight / food['weight']
                total_calories += round(food['total_calories'] * ratio, 1)
                total_protein += round(food['total_protein'] * ratio, 1)
                total_carbs += round(food['total_carbs'] * ratio, 1)
                total_fat += round(food['total_fat'] * ratio, 1)
                food_names.append(food['food_name'])

            log = self.user.eat(
                food_name="、".join(food_names),
                total_calories=total_calories,
                protein=total_protein,
                carbs=total_carbs,
                fat=total_fat
            )

            self.main_screen.update_display()
            popup.dismiss()
        except Exception as e:
            pass

    def show_diet_recommendation(self, instance):
        if not self.user:
            return
        popup = Popup(title="🥗 今日饮食推荐", size_hint=(0.9, 0.8))
        layout = BoxLayout(orientation='vertical', padding=10, spacing=10)

        rec = self.user.get_diet_recommendation()
        layout.add_widget(Label(text=f"目标热量: {rec['target_calories']} 大卡", font_size=18))
        layout.add_widget(
            Label(text=f"蛋白质: {rec['target_protein']}g | 碳水: {rec['target_carbs']}g | 脂肪: {rec['target_fat']}g",
                  font_size=16))

        layout.add_widget(Label(text="\n推荐餐单:", font_size=18))
        for meal in rec['recommendation']:
            layout.add_widget(Label(text=f"• {meal}", font_size=16))

        layout.add_widget(
            Label(text=f"\n今日摄入: {self.user.daily_calories_intake}/{rec['target_calories']} 大卡", font_size=16))

        close_btn = Button(text="关闭", size_hint_y=0.1, background_color=(0.7, 0.7, 0.7, 1))
        close_btn.bind(on_press=popup.dismiss)
        layout.add_widget(close_btn)

        popup.content = layout
        popup.open()

    def show_food_history(self, instance):
        if not self.user:
            return
        popup = Popup(title="📜 饮食历史", size_hint=(0.95, 0.95))
        layout = BoxLayout(orientation='vertical', padding=10, spacing=10)

        scroll = ScrollView(size_hint=(1, 0.7))
        list_layout = GridLayout(cols=1, spacing=5, size_hint_y=None)
        list_layout.bind(minimum_height=list_layout.setter('height'))

        for record in reversed(self.user.food_history[-20:]):
            list_layout.add_widget(
                Label(text=f"{record['time']} | {record['food_name']} | {record['calories']}大卡", size_hint_y=None,
                      height=40))

        scroll.add_widget(list_layout)
        layout.add_widget(scroll)

        close_btn = Button(text="关闭", size_hint_y=0.1, background_color=(0.7, 0.7, 0.7, 1))
        close_btn.bind(on_press=popup.dismiss)
        layout.add_widget(close_btn)

        popup.content = layout
        popup.open()

    def show_band_scan(self, instance):
        self.sm.current = 'main'
        self.show_band_scan_screen()

    def show_band_scan_screen(self):
        popup = Popup(title="📱 连接手环", size_hint=(0.9, 0.8))
        layout = BoxLayout(orientation='vertical', padding=10, spacing=10)

        scroll = ScrollView(size_hint=(1, 0.6))
        self.device_list_layout = GridLayout(cols=1, spacing=5, size_hint_y=None)
        self.device_list_layout.bind(minimum_height=self.device_list_layout.setter('height'))
        scroll.add_widget(self.device_list_layout)
        layout.add_widget(scroll)

        scan_btn = Button(text="🔍 开始扫描", size_hint_y=0.1, background_color=(0.3, 0.5, 0.9, 1))
        scan_btn.bind(on_press=self.scan_band_devices)
        layout.add_widget(scan_btn)

        close_btn = Button(text="关闭", size_hint_y=0.1, background_color=(0.7, 0.7, 0.7, 1))
        close_btn.bind(on_press=popup.dismiss)
        layout.add_widget(close_btn)

        self.band_scan_popup = popup
        popup.content = layout
        popup.open()

    def scan_band_devices(self, instance):
        self.device_list_layout.clear_widgets()

        def run_scan(dt):
            devices = loop.run_until_complete(self.band_manager.scan_devices())
            if not devices:
                self.device_list_layout.add_widget(Label(text="未找到设备", size_hint_y=None, height=40))
                return
            for dev in devices:
                btn = Button(text=f"{dev.name} | {dev.address}", size_hint_y=None, height=50)
                btn.bind(on_press=lambda x, addr=dev.address: self.connect_band(addr))
                self.device_list_layout.add_widget(btn)

        Clock.schedule_once(run_scan, 0.1)

    def connect_band(self, device_address):
        def run_connect(dt):
            success = loop.run_until_complete(self.band_manager.connect_device(device_address))
            if success:
                self.band_scan_popup.dismiss()

        Clock.schedule_once(run_connect, 0.1)

    def show_exercise(self, instance):
        self.sm.current = 'main'
        self.show_exercise_screen()

    def show_exercise_screen(self):
        popup = Popup(title="🏃 手动锻炼", size_hint=(0.9, 0.8))
        layout = BoxLayout(orientation='vertical', padding=10, spacing=10)

        exercise_types = [('跑步', 'run'), ('健身', 'gym'), ('游泳', 'swim'), ('骑行', 'cycle')]
        for name, etype in exercise_types:
            btn = Button(text=name, size_hint_y=0.15)
            btn.bind(on_press=lambda x, e=etype: self.do_exercise(e, popup))
            layout.add_widget(btn)

        close_btn = Button(text="关闭", size_hint_y=0.1, background_color=(0.7, 0.7, 0.7, 1))
        close_btn.bind(on_press=popup.dismiss)
        layout.add_widget(close_btn)

        popup.content = layout
        popup.open()

    def do_exercise(self, exercise_type, popup):
        result = self.user.exercise(exercise_type, 30)
        self.main_screen.update_display()
        popup.dismiss()

    def show_sleep(self, instance):
        self.sm.current = 'main'
        self.show_sleep_screen()

    def show_sleep_screen(self):
        popup = Popup(title="😴 睡眠记录", size_hint=(0.9, 0.7))
        layout = BoxLayout(orientation='vertical', padding=10, spacing=10)

        layout.add_widget(Label(text="总睡眠时长(小时):", size_hint_y=0.1))
        sleep_hours_input = TextInput(multiline=False, input_filter='float', size_hint_y=0.1)
        layout.add_widget(sleep_hours_input)

        layout.add_widget(Label(text="深度睡眠时长(小时):", size_hint_y=0.1))
        deep_sleep_input = TextInput(multiline=False, input_filter='float', size_hint_y=0.1)
        layout.add_widget(deep_sleep_input)

        confirm_btn = Button(text="确认", size_hint_y=0.1, background_color=(0.6, 0.3, 0.9, 1))
        confirm_btn.bind(on_press=lambda x: self.confirm_sleep(sleep_hours_input, deep_sleep_input, popup))
        layout.add_widget(confirm_btn)

        close_btn = Button(text="关闭", size_hint_y=0.1, background_color=(0.7, 0.7, 0.7, 1))
        close_btn.bind(on_press=popup.dismiss)
        layout.add_widget(close_btn)

        popup.content = layout
        popup.open()

    def confirm_sleep(self, hours_input, deep_input, popup):
        try:
            self.user.update_sleep_data(float(hours_input.text), float(deep_input.text))
            self.main_screen.update_display()
            popup.dismiss()
        except:
            pass

    def show_body_history(self, instance):
        self.sm.current = 'main'
        self.show_history_screen()

    def show_history_screen(self):
        if not self.user:
            return
        popup = Popup(title="📈 身体历史", size_hint=(0.95, 0.95))
        layout = BoxLayout(orientation='vertical', padding=10, spacing=10)

        scroll = ScrollView(size_hint=(1, 0.8))
        list_layout = GridLayout(cols=1, spacing=5, size_hint_y=None)
        list_layout.bind(minimum_height=list_layout.setter('height'))

        for record in reversed(self.user.history[-20:]):
            list_layout.add_widget(Label(
                text=f"{record['time']} | 体重:{record['weight']}kg | 体脂:{record['body_fat']}% | 肌肉:{record['muscle_mass']}kg",
                size_hint_y=None, height=40))

        scroll.add_widget(list_layout)
        layout.add_widget(scroll)

        close_btn = Button(text="关闭", size_hint_y=0.1, background_color=(0.7, 0.7, 0.7, 1))
        close_btn.bind(on_press=popup.dismiss)
        layout.add_widget(close_btn)

        popup.content = layout
        popup.open()

    def show_goal_setting(self, instance):
        self.sm.current = 'main'
        self.show_goal_screen()

    def show_goal_screen(self):
        popup = Popup(title="⚙️ 设置目标", size_hint=(0.9, 0.6))
        layout = BoxLayout(orientation='vertical', padding=10, spacing=10)

        layout.add_widget(Label(text="选择目标:", font_size=18))
        goals = [('增肌', 'gain'), ('减脂', 'lose'), ('维持', 'maintain')]
        for name, goal in goals:
            btn = Button(text=name, size_hint_y=0.15)
            btn.bind(on_press=lambda x, g=goal: self.set_goal(g, popup))
            layout.add_widget(btn)

        close_btn = Button(text="关闭", size_hint_y=0.1, background_color=(0.7, 0.7, 0.7, 1))
        close_btn.bind(on_press=popup.dismiss)
        layout.add_widget(close_btn)

        popup.content = layout
        popup.open()

    def set_goal(self, goal, popup):
        if self.user:
            self.user.goal = goal
        popup.dismiss()

    def toggle_auto_sync(self, instance):
        if not self.user:
            return

        if self.auto_sync_event is None:
            self.auto_sync_event = Clock.schedule_interval(self.auto_sync, 15 * 60)
            self.auto_sync(0)
        else:
            self.auto_sync_event.cancel()
            self.auto_sync_event = None

    def auto_sync(self, dt):
        if not self.band_manager.is_connected or not self.user:
            return

        def run_sync(dt):
            steps = loop.run_until_complete(self.band_manager.read_step_data())
            band_data = self.band_manager.get_realtime_data()
            log = self.user.auto_update_from_band(
                heart_rate=band_data['heart_rate'],
                total_steps=steps,
                systolic_bp=band_data['systolic_bp'],
                diastolic_bp=band_data['diastolic_bp']
            )
            self.main_screen.update_display()

            loop.run_until_complete(self.band_manager.send_data_to_band_display(
                self.user.fat_level,
                self.user.muscle_level,
                self.user.strength,
                self.user.stamina,
                self.user.realtime_heart_rate
            ))

        Clock.schedule_once(run_sync, 0.1)

    def on_stop(self):
        if self.band_manager.is_connected:
            loop.run_until_complete(self.band_manager.disconnect_device())
        if self.auto_sync_event:
            self.auto_sync_event.cancel()
        if os.path.exists(TEMP_IMAGE_PATH):
            try:
                os.remove(TEMP_IMAGE_PATH)
            except:
                pass
        super().on_stop()


if __name__ == "__main__":
    GTABodyFitnessApp().run()