from aiogram import Bot, Dispatcher, Router
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
import asyncio
import os
from dotenv import load_dotenv
from db import DB

load_dotenv()

bot = Bot(token=os.environ.get('TOKEN'))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

db = DB()

registered_employees = {}  # {user_id: place_id}
pending_orders = {}  # {order_id: order_info}

@router.message(Command('start'))
async def start(message: Message):
    builder = ReplyKeyboardBuilder()
    builder.button(text="📝 Зарегистрироваться")
    
    await message.answer(
        "👋 Добро пожаловать в систему управления заказами!\n"
        "Нажмите кнопку ниже, чтобы зарегистрироваться как сотрудник:",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )

@router.message(lambda message: message.text == "📝 Зарегистрироваться")
async def register_handler(message: Message):
    await message.answer(
        "🔐 Для регистрации введите ID места в формате:\n"
        "<code>/register ID_места</code>\n\n"
        "Например: <code>/register 1</code>",
        parse_mode="HTML"
    )

@router.message(Command('register'))
async def register(message: Message):
    args = message.text.split()[1] if len(message.text.split()) > 1 else None
    
    if not args:
        await message.answer(
            "❌ Не указан ID места\n"
            "Формат: <code>/register ID_места</code>\n"
            "Пример: <code>/register 1</code>",
            parse_mode="HTML"
        )
        return
    
    try:
        place_id = int(args.strip())
        
        if place_id <= 0:
            raise ValueError("ID места должен быть положительным числом")
            
        registered_employees[message.from_user.id] = place_id
        await message.answer(
            f"✅ <b>Регистрация успешно завершена!</b>\n"
            f"Вы зарегистрированы для места с ID: <code>{place_id}</code>\n\n"
            f"Теперь вы будете получать уведомления о новых заказах.",
            parse_mode="HTML"
        )
        
    except ValueError as e:
        await message.answer(
            f"❌ <b>Ошибка регистрации</b>\n"
            f"{str(e)}\n\n"
            f"Правильный формат: <code>/register ID_места</code>\n"
            f"Пример: <code>/register 1</code>",
            parse_mode="HTML"
        )

async def handle_new_order(order_info):
    if not order_info:
        return
        
    place_id = order_info.get('place_id')
    order_id = order_info.get('order_id')
    product_name = order_info.get('name')
    
    if not all([place_id, order_id, product_name]):
        return
    
    pending_orders[order_id] = order_info
    
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Одобрить", callback_data=f"approve_{order_id}")
    builder.button(text="❌ Отклонить", callback_data=f"reject_{order_id}")
    
    for user_id, emp_place_id in registered_employees.items():
        if emp_place_id == place_id:
            try:
                await bot.send_message(
                    user_id,
                    f"📦 <b>Новый заказ!</b>\n"
                    f"├ ID заказа: <code>{order_id}</code>\n"
                    f"├ Товар: {product_name}\n"
                    f"└ Место: <code>{place_id}</code>\n\n"
                    f"Выберите действие:",
                    parse_mode="HTML",
                    reply_markup=builder.as_markup()
                )
            except Exception as e:
                print(f"Ошибка при отправке сообщения пользователю {user_id}: {e}")

@router.callback_query(lambda c: c.data.startswith('approve_'))
async def approve_order(callback: CallbackQuery):
    order_id = callback.data.split('_')[1]
    order_info = pending_orders.get(order_id)
    
    if not order_info:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    
    try:
        pending_orders.pop(order_id, None)
        await db.update_order_status(order_id, "success")
        
        await callback.message.edit_text(
            f"✅ <b>Заказ одобрен</b>\n"
            f"├ ID: <code>{order_id[:8]}...</code>\n"
            f"└ Товар: {order_info['name']}",
            parse_mode="HTML"
        )
        await callback.answer()
        
    except Exception as e:
        await callback.answer("Ошибка при одобрении", show_alert=True)
        print(f"Ошибка при одобрении заказа {order_id}: {e}")

@router.callback_query(lambda c: c.data.startswith('reject_'))
async def reject_order(callback: CallbackQuery):
    order_id = callback.data.split('_')[1]
    order_info = pending_orders.get(order_id)
    
    if not order_info:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    
    try:
        pending_orders.pop(order_id, None)
        user_id = await db.get_order_user_id(order_id)
        refund_amount = await db.get_order_price(order_id)
        
        if not user_id:
            raise ValueError("Не удалось получить данные для возврата")
        
        await db.update_order_status(order_id, "cancel")
        await db.refund_user_coins(user_id, refund_amount)
        
        await callback.message.edit_text(
            f"❌ <b>Заказ отклонён</b>\n"
            f"├ ID: <code>{order_id[:8]}...</code>\n"
            f"├ Товар: {order_info['name']}\n"
            f"└ Возвращено: <code>{refund_amount}</code> coins",
            parse_mode="HTML"
        )
        await callback.answer()
        
    except Exception as e:
        await callback.answer(f"Ошибка при отклонении: {str(e)}", show_alert=True)
        print(f"Ошибка при отклонении заказа {order_id}: {e}")

async def run_db_listener():
    await db.initialize()
    await db.watch_new_order_items(handle_new_order)

async def on_startup():
    asyncio.create_task(run_db_listener())

async def main():
    await on_startup()
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())