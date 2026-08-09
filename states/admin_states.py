from aiogram.fsm.state import State, StatesGroup


class DiscountStates(StatesGroup):
    waiting_code = State()
    waiting_percent = State()
    waiting_max_uses = State()


class StockAddStates(StatesGroup):
    waiting_config_name = State()
    waiting_config_link = State()


class PanelAddStates(StatesGroup):
    waiting_name = State()
    waiting_type = State()
    waiting_url = State()
    waiting_username = State()
    waiting_password = State()


class CategoryAddStates(StatesGroup):
    waiting_title = State()
    waiting_prefix = State()


class PlanAddStates(StatesGroup):
    waiting_category = State()
    waiting_new_duration_label = State()
    waiting_new_duration_days = State()
    waiting_name = State()
    waiting_volume = State()
    waiting_price = State()
    waiting_delivery_mode = State()
    waiting_hwid = State()
    waiting_duration = State()


class ServiceEditStates(StatesGroup):
    waiting_new_price = State()
    waiting_new_hwid = State()
    waiting_new_duration = State()
    waiting_new_name = State()


class CategoryEditStates(StatesGroup):
    waiting_new_title = State()
    waiting_new_prefix = State()


class SettingsEditStates(StatesGroup):
    waiting_new_value = State()


class BroadcastStates(StatesGroup):
    waiting_message = State()


class MenuEditStates(StatesGroup):
    waiting_custom_label = State()
    waiting_custom_response = State()
    waiting_icon_emoji = State()


class FindUserStates(StatesGroup):
    waiting_user_id = State()


class SubMergeStates(StatesGroup):
    waiting_base_url = State()
    waiting_username = State()
    waiting_password = State()
    waiting_display_name = State()
    waiting_support_channel = State()


class MessageUserStates(StatesGroup):
    waiting_user_id = State()
    waiting_message = State()


class PanelGroupsStates(StatesGroup):
    waiting_group_ids = State()


class PanelMarzbanStates(StatesGroup):
    waiting_protocol = State()
    waiting_inbound_tags = State()


class TrialAddStates(StatesGroup):
    waiting_name = State()
    waiting_volume_mb = State()
    waiting_duration_days = State()
    waiting_panel = State()


class TrialEditStates(StatesGroup):
    waiting_new_name = State()
    waiting_new_volume_mb = State()
    waiting_new_duration_days = State()


class CategoryDurationStates(StatesGroup):
    waiting_label = State()
    waiting_days = State()


class GatewayConfigStates(StatesGroup):
    waiting_api_key = State()
    waiting_api_secret = State()


class CryptoConfigStates(StatesGroup):
    waiting_ton_address = State()
    waiting_bsc_address = State()
    waiting_ton_api_key = State()
    waiting_bscscan_api_key = State()
    waiting_comment_prompt = State()


class WalletAdjustStates(StatesGroup):
    waiting_user_id = State()
    waiting_amount = State()
    waiting_note = State()


class DeliverStates(StatesGroup):
    waiting_config_name = State()
    waiting_config_link = State()
