import os
import asyncio
from supabase import acreate_client, AsyncClient
from dotenv import load_dotenv

load_dotenv()

class DB:
    def __init__(self):
        self.url: str = os.environ.get("SUPABASE_URL")
        self.key: str = os.environ.get("SUPABASE_KEY")
        self.supabase: AsyncClient = None
        self.order_handler = None

    async def initialize(self):
        if not self.url or not self.key:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in environment variables or .env file.")
        self.supabase = await acreate_client(self.url, self.key)
        print("Supabase async client initialized successfully.")

    async def get_employee_by_uuid_and_place(self, employee_uuid: str, place_id: int) -> dict:
        """Проверяет существование связки employee_id + place_id"""
        try:
            response = await self.supabase.table('partners_and_places_link') \
                                    .select('*') \
                                    .eq('employee_id', employee_uuid) \
                                    .eq('id', place_id) \
                                    .maybe_single() \
                                    .execute()
            return response.data if response.data else None
        except Exception as e:
            print(f"Ошибка при проверке связки сотрудник-место: {e}")
            return None

    async def update_order_status(self, order_id: str, status: str):
        """Обновляет только статус заказа в таблице orders"""
        await self.supabase.table('orders') \
            .update({'status': status}) \
            .eq('id', order_id) \
            .execute()

    async def refund_order(self, order_id: str):
        """Возврат средств за заказ (обновляет только таблицу coins)"""
        user_id = await self.get_order_user_id(order_id)
        if not user_id:
            raise ValueError("User not found for this order")
        
        refund_amount = await self.get_order_price(order_id)
        if not refund_amount:
            raise ValueError("Order price not found")
        
        await self.refund_user_coins(user_id, refund_amount)

    async def get_order_price(self, order_id: str) -> float:
        """Получает общую сумму заказа (order_id теперь строка)"""
        response = await self.supabase.table('order_items') \
                                     .select('price') \
                                     .eq('order_id', order_id) \
                                     .execute()
        return sum(item['price'] for item in response.data) if response.data else 0

    async def get_order_user_id(self, order_id: str) -> int:
        """Получает user_id из заказа (order_id теперь строка)"""
        response = await self.supabase.table('orders') \
                                     .select('user_id') \
                                     .eq('id', order_id) \
                                     .single() \
                                     .execute()
        return response.data.get('user_id') if response.data else None

    async def refund_user_coins(self, user_id: int, amount: float):
        """Возвращает coins пользователю"""
        try:
            response = await self.supabase.table('coins') \
                                        .select('coins') \
                                        .eq('user_id', user_id) \
                                        .single() \
                                        .execute()
            
            if not response.data:
                await self.supabase.table('coins') \
                                .insert({'user_id': user_id, 'coins': amount}) \
                                .execute()
            else:
                current_coins = response.data.get('coins', 0)
                new_coins = current_coins + amount
                
                await self.supabase.table('coins') \
                                .update({'coins': new_coins}) \
                                .eq('user_id', user_id) \
                                .execute()
        except Exception as e:
            print(f"Ошибка при возврате средств пользователю {user_id}: {e}")
            raise

    async def _handle_new_order_item(self, payload: dict, ref=None):
        new_order_item = payload.get('data', {}).get('record', {})

        if not new_order_item:
            print(f"Ошибка: 'record' ключ в payload.data пуст или отсутствует. payload: {payload}")
            return

        order_item_id = new_order_item.get('id')
        if not order_item_id:
            print(f"Ошибка: Новая запись order_items не содержит ID. Данные: {new_order_item}")
            return

        try:
            response = await self.supabase.table('order_items') \
                                       .select('order_id, partners_products(name, partners_produts_at_place!fk_partner_product_id(place_id))') \
                                       .eq('id', order_item_id) \
                                       .single() \
                                       .execute()

            if response.data:
                final_order_id = response.data['order_id'] 
                print(response.data)
                partner_product_data = response.data.get('partners_products', {})
                product_name = partner_product_data.get('name')

                partners_products_at_place_list = partner_product_data.get('partners_produts_at_place', [])
                place_id = None
                
                if partners_products_at_place_list:
                    first_place_entry = partners_products_at_place_list[0]
                    place_id = first_place_entry.get('place_id')

                if final_order_id and product_name and place_id:
                    if self.order_handler:
                        order_info = {
                            'order_id': final_order_id,
                            'name': product_name,
                            'place_id': place_id
                        }
                        await self.order_handler(order_info)

        except Exception as e:
            print(f"Ошибка при обработке нового заказа: {e}")

    def _handle_new_order_item_wrapper(self, payload: dict, ref=None):
        asyncio.create_task(self._handle_new_order_item(payload, ref))

    async def watch_new_order_items(self, order_handler=None):
        if not self.supabase:
            raise RuntimeError("Supabase client not initialized. Call db.initialize() first.")
        
        self.order_handler = order_handler
        
        print("Starting to listen for INSERT events on 'order_items'...")
        subscription = self.supabase.realtime.channel('order_items_channel')
        subscription.on_postgres_changes(
            'INSERT',
            schema='public',
            table='order_items',
            callback=self._handle_new_order_item_wrapper
        )
        try:
            await subscription.subscribe()
            print("Successfully subscribed to changes.")
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            print("Listening cancelled.")
        except Exception as e:
            print(f"Error occurred during listening: {e}")
        finally:
            if subscription:
                await subscription.unsubscribe()
                print("Channel unsubscribed.")