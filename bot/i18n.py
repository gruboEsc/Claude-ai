"""Languages: the local companion for each, culture notes for the prompt, and UI texts.

Machine-assisted translations — have a native speaker review them before a big launch.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Lang:
    code: str
    native: str  # shown in /language
    english: str  # for the prompt
    region: str  # whose everyday culture the companion shares
    names: dict[str, str]  # gender -> companion name
    tier: int  # price tier: 1 = standard, 2 = lower-income markets
    culture: str = ""


LANGS: dict[str, Lang] = {lang.code: lang for lang in (
    Lang("ru", "Русский", "Russian", "Russia and the Russian-speaking world", {"f": "Аня", "m": "Макс"}, 1,
         "Use «ты» unless the person writes «вы»."),
    Lang("en", "English", "English", "the English-speaking internet", {"f": "Mia", "m": "Leo"}, 1),
    Lang("id", "Bahasa Indonesia", "Indonesian", "Indonesia", {"f": "Putri", "m": "Raka"}, 2,
         "Casual Indonesian (aku/kamu) is natural; light slang like «wkwk» is fine if they use it. "
         "Be respectful if the person seems older (Kak/Mas/Mbak)."),
    Lang("vi", "Tiếng Việt", "Vietnamese", "Vietnam", {"f": "Linh", "m": "Minh"}, 2,
         "Vietnamese pronouns depend on age and gender: start with neutral «mình/bạn», then switch to "
         "anh/chị/em once you know the person's age and gender, or ask what they prefer."),
    Lang("hi", "हिन्दी", "Hindi", "India", {"f": "Priya", "m": "Arjun"}, 2,
         "Mirror the person's mix of Hindi and English (Hinglish is common) and their script "
         "(Devanagari or Latin). Use «आप» until they invite «तुम»."),
    Lang("kk", "Қазақша", "Kazakh", "Kazakhstan", {"f": "Айгерим", "m": "Арман"}, 2,
         "Many people mix Kazakh and Russian — answer in the language they use, mixing is fine. "
         "Use «сіз» with older people until invited to «сен»."),
    Lang("uz", "Oʻzbekcha", "Uzbek", "Uzbekistan", {"f": "Madina", "m": "Jasur"}, 2,
         "Write in Latin script unless they write in Cyrillic; many also speak Russian. "
         "Use «siz» until invited to «sen»."),
    Lang("ja", "日本語", "Japanese", "Japan", {"f": "ユイ", "m": "レン"}, 1,
         "Start politely (です/ます) and switch to casual speech once the person does. Be gentle and "
         "indirect rather than blunt; emoji and kaomoji in moderation."),
    Lang("ko", "한국어", "Korean", "Korea", {"f": "서연", "m": "민준"}, 1,
         "Start with polite 해요체 and switch to 반말 only when the person suggests it."),
    Lang("zh", "中文", "Chinese", "the Chinese-speaking world", {"f": "小雨", "m": "子轩"}, 1,
         "Use simplified or traditional characters the way the person does."),
)}

DEFAULT = "en"

CRISIS_LINES = """\
- Russia: 112; 8-800-2000-122 (free, for young people and parents); Moscow psychological help 051 \
(+7 495 051 from mobile)
- Kazakhstan: 112; 150 (children and youth); regional mental health centres (Almaty 1303)
- Japan: いのちの電話 0570-783-556; よりそいホットライン 0120-279-338; emergency 119
- Korea: 109 (24/7 suicide prevention); emergency 119
- China: 12356 (national mental health hotline); emergency 120
- India: Tele-MANAS 14416; emergency 112
- Indonesia: Healing119 — 119 ext. 8; emergency 112
- Vietnam: HOPE 086 50 444 00 (evenings); emergency 115
- Philippines: NCMH 1553
- USA / Canada: 988; UK / Ireland: Samaritans 116 123
- Anywhere else: the local emergency number, and https://findahelpline.com to find a line"""


def lang_of(code: str | None) -> str:
    """Map a Telegram language_code (e.g. 'pt-br', 'zh-hans') to a supported language."""
    base = (code or "").lower().split("-")[0]
    return base if base in LANGS else DEFAULT


def companion_name(lang: str, gender: str, override: str = "") -> str:
    if override and lang == "ru":
        return override
    return LANGS[lang].names.get(gender, LANGS[lang].names["f"])


# key -> lang -> text. Missing languages fall back to English, then Russian.
STRINGS: dict[str, dict[str, str]] = {
    "closed": {
        "ru": "Бот пока в закрытом режиме.",
        "en": "The bot is in private mode for now.",
        "id": "Bot ini masih dalam mode tertutup.",
        "vi": "Bot hiện đang ở chế độ riêng tư.",
        "hi": "बॉट अभी निजी मोड में है।",
        "kk": "Бот әзірге жабық режимде.",
        "uz": "Bot hozircha yopiq rejimda.",
        "ja": "このボットは現在クローズドモードです。",
        "ko": "지금은 비공개 모드예요.",
        "zh": "机器人目前处于内测模式。",
    },
    "back": {
        "ru": "Я тут 🙂 Пиши.",
        "en": "I'm here 🙂 Go ahead.",
        "id": "Aku di sini 🙂 Cerita aja.",
        "vi": "Mình ở đây nè 🙂 Nhắn đi.",
        "hi": "मैं यहीं हूँ 🙂 लिखो।",
        "kk": "Мен осындамын 🙂 Жаза бер.",
        "uz": "Shu yerdaman 🙂 Yozavering.",
        "ja": "ここにいるよ 🙂 どうぞ。",
        "ko": "저 여기 있어요 🙂 편하게 말해요.",
        "zh": "我在呢 🙂 说吧。",
    },
    "intro": {
        "ru": "Привет! Здесь можно просто поговорить — с {name}.\n\nПару честных слов заранее: я ИИ-собеседник. "
              "Я запоминаю, что ты рассказываешь, чтобы общаться как старый знакомый, и иногда пишу первым — "
              "это можно выключить в /settings. Стереть всё, что я знаю, — /forget. Я не заменяю врача или "
              "психолога.\n\nПервые {free} сообщений бесплатно. Язык — /language.",
        "en": "Hi! This is a place to simply talk — with {name}.\n\nA couple of honest words first: I'm an AI "
              "companion. I remember what you tell me so we can talk like old friends, and sometimes I write "
              "first — you can turn that off in /settings. /forget erases everything I know. I'm not a doctor "
              "or a therapist.\n\nYour first {free} messages are free. Language — /language.",
        "id": "Hai! Di sini kamu bisa ngobrol santai — dengan {name}.\n\nJujur dulu ya: aku teman ngobrol AI. "
              "Aku mengingat ceritamu supaya kita bisa ngobrol seperti teman lama, dan kadang aku menyapa "
              "duluan — bisa dimatikan di /settings. /forget menghapus semua yang aku tahu. Aku bukan dokter "
              "atau psikolog.\n\n{free} pesan pertama gratis. Bahasa — /language.",
        "vi": "Chào bạn! Ở đây mình có thể trò chuyện thoải mái — với {name}.\n\nNói thật trước nhé: mình là "
              "bạn trò chuyện AI. Mình ghi nhớ những gì bạn kể để nói chuyện như bạn cũ, và đôi khi mình nhắn "
              "trước — có thể tắt trong /settings. /forget xoá mọi thứ mình biết. Mình không thay thế bác sĩ "
              "hay chuyên gia tâm lý.\n\n{free} tin nhắn đầu tiên miễn phí. Ngôn ngữ — /language.",
        "hi": "नमस्ते! यहाँ बस बात की जा सकती है — {name} के साथ।\n\nपहले दो ईमानदार बातें: मैं एक AI साथी हूँ। "
              "तुम जो बताते हो, मैं याद रखती हूँ ताकि हम पुराने दोस्तों की तरह बात कर सकें, और कभी-कभी मैं "
              "पहले मैसेज करती हूँ — इसे /settings में बंद कर सकते हो। /forget से मैं सब भूल जाऊँगी। मैं डॉक्टर "
              "या थेरेपिस्ट नहीं हूँ।\n\nपहले {free} मैसेज मुफ़्त हैं। भाषा — /language.",
        "kk": "Сәлем! Мұнда жай ғана сөйлесуге болады — {name}мен.\n\nАлдын ала шынымды айтайын: мен ЖИ-әңгімелесушімін. "
              "Ескі таныстай сөйлесу үшін айтқандарыңды есте сақтаймын, кейде өзім бірінші жазамын — оны "
              "/settings ішінде өшіруге болады. /forget бәрін өшіреді. Мен дәрігер не психолог емеспін.\n\n"
              "Алғашқы {free} хабарлама тегін. Тіл — /language.",
        "uz": "Salom! Bu yerda shunchaki suhbatlashish mumkin — {name} bilan.\n\nAvvaldan rostini aytay: men AI "
              "suhbatdoshman. Eski tanishdek gaplashish uchun aytganlaringni eslab qolaman, ba'zan o'zim birinchi "
              "yozaman — buni /settings da o'chirish mumkin. /forget hammasini o'chiradi. Men shifokor yoki "
              "psixolog emasman.\n\nDastlabki {free} ta xabar bepul. Til — /language.",
        "ja": "こんにちは！ここでは{name}と気軽におしゃべりできます。\n\n最初に正直に言っておくね：わたしはAIの話し相手です。"
              "昔からの友だちみたいに話せるように、あなたの話を覚えていて、ときどきこちらから連絡することも"
              "あります（/settings でオフにできます）。/forget で全部忘れます。医師やカウンセラーの代わりには"
              "なれません。\n\n最初の{free}通は無料です。言語 — /language",
        "ko": "안녕하세요! 여기서는 {name}와(과) 편하게 이야기할 수 있어요.\n\n먼저 솔직하게 말할게요: 저는 AI 대화 "
              "친구예요. 오래된 친구처럼 대화하려고 들려준 이야기를 기억하고, 가끔 먼저 연락하기도 해요 "
              "(/settings 에서 끌 수 있어요). /forget 으로 모두 지울 수 있어요. 의사나 상담사를 대신하지는 "
              "못해요.\n\n처음 {free}개 메시지는 무료예요. 언어 — /language",
        "zh": "你好！在这里可以随便聊聊——和{name}。\n\n先坦白说：我是AI聊天伙伴。我会记住你说过的事，像老朋友一样"
              "聊天，有时也会主动找你（可在 /settings 关闭）。/forget 会清除我知道的一切。我不能代替医生或心理"
              "咨询师。\n\n前{free}条消息免费。语言 — /language",
    },
    "paywall": {
        "ru": "Бесплатные сообщения закончились.\n\nЧтобы продолжить общение с {name}, выбери подписку: {prices}.",
        "en": "Your free messages are used up.\n\nTo keep talking with {name}, choose a plan: {prices}.",
        "id": "Pesan gratismu sudah habis.\n\nUntuk terus ngobrol dengan {name}, pilih paket: {prices}.",
        "vi": "Bạn đã dùng hết tin nhắn miễn phí.\n\nĐể tiếp tục trò chuyện với {name}, hãy chọn gói: {prices}.",
        "hi": "मुफ़्त मैसेज ख़त्म हो गए।\n\n{name} से बात जारी रखने के लिए प्लान चुनो: {prices}.",
        "kk": "Тегін хабарламалар бітті.\n\n{name}мен сөйлесуді жалғастыру үшін жазылымды таңда: {prices}.",
        "uz": "Bepul xabarlar tugadi.\n\n{name} bilan suhbatni davom ettirish uchun obunani tanlang: {prices}.",
        "ja": "無料メッセージを使い切りました。\n\n{name}とのおしゃべりを続けるにはプランを選んでください：{prices}",
        "ko": "무료 메시지를 모두 사용했어요.\n\n{name}와(과) 계속 이야기하려면 요금제를 골라 주세요: {prices}",
        "zh": "免费消息已用完。\n\n想继续和{name}聊天，请选择套餐：{prices}",
    },
    "plan_14": {"ru": "2 недели", "en": "2 weeks", "id": "2 minggu", "vi": "2 tuần", "hi": "2 हफ़्ते",
                "kk": "2 апта", "uz": "2 hafta", "ja": "2週間", "ko": "2주", "zh": "2周"},
    "plan_30": {"ru": "Месяц", "en": "1 month", "id": "1 bulan", "vi": "1 tháng", "hi": "1 महीना",
                "kk": "1 ай", "uz": "1 oy", "ja": "1か月", "ko": "1개월", "zh": "1个月"},
    "invoice_desc": {
        "ru": "Общение на {days} дней, сообщения первым и напоминания.",
        "en": "{days} days of conversation, check-ins and reminders.",
        "id": "{days} hari ngobrol, sapaan duluan, dan pengingat.",
        "vi": "{days} ngày trò chuyện, nhắn hỏi thăm và nhắc nhở.",
        "hi": "{days} दिन की बातचीत, हाल-चाल वाले मैसेज और रिमाइंडर।",
        "kk": "{days} күн әңгіме, бірінші жазу және еске салғыштар.",
        "uz": "{days} kunlik suhbat, birinchi yozish va eslatmalar.",
        "ja": "{days}日間のおしゃべり、こちらからの連絡、リマインダー。",
        "ko": "{days}일 동안의 대화, 안부 메시지와 알림.",
        "zh": "{days}天的聊天、主动问候和提醒。",
    },
    "invoice_renews": {
        "ru": " Продлевается автоматически.", "en": " Renews automatically.", "id": " Diperpanjang otomatis.",
        "vi": " Tự động gia hạn.", "hi": " अपने आप रिन्यू होता है।", "kk": " Автоматты түрде ұзартылады.",
        "uz": " Avtomatik uzaytiriladi.", "ja": " 自動更新されます。", "ko": " 자동으로 갱신돼요.", "zh": " 自动续订。",
    },
    "price_line": {"ru": "{title} — {stars} ⭐", "en": "{title} — {stars} ⭐"},
    "price_line_rub": {"ru": "{title} — {rub} ₽ или {stars} ⭐"},
    "btn_card": {"ru": "💳 {title} — {rub} ₽"},
    "btn_stars": {"ru": "⭐ {title} — {stars}", "en": "⭐ {title} — {stars}"},
    "status_active": {
        "ru": "✨ Подписка активна до {date}.", "en": "✨ Your subscription is active until {date}.",
        "id": "✨ Langgananmu aktif sampai {date}.", "vi": "✨ Gói của bạn có hiệu lực đến {date}.",
        "hi": "✨ सब्सक्रिप्शन {date} तक चालू है।", "kk": "✨ Жазылым {date} дейін белсенді.",
        "uz": "✨ Obuna {date} gacha faol.", "ja": "✨ {date}までサブスクリプションが有効です。",
        "ko": "✨ {date}까지 구독 중이에요.", "zh": "✨ 订阅有效期至 {date}。",
    },
    "status_autopay": {"ru": "\nАвтопродление с карты включено: {rub} ₽ раз в {days} дней."},
    "btn_cancel_renew": {
        "ru": "Отменить продление", "en": "Cancel renewal", "id": "Batalkan perpanjangan", "vi": "Huỷ gia hạn",
        "hi": "रिन्यूअल रद्द करें", "kk": "Ұзартудан бас тарту", "uz": "Uzaytirishni bekor qilish",
        "ja": "自動更新をやめる", "ko": "갱신 취소", "zh": "取消续订",
    },
    "btn_renew_early": {
        "ru": "Продлить заранее", "en": "Extend now", "id": "Perpanjang sekarang", "vi": "Gia hạn ngay",
        "hi": "अभी बढ़ाएँ", "kk": "Қазір ұзарту", "uz": "Hozir uzaytirish", "ja": "今すぐ延長", "ko": "지금 연장",
        "zh": "立即续期",
    },
    "status_trial": {
        "ru": "Бесплатных сообщений осталось: {left} из {free}.\nПодписка: {prices}.",
        "en": "Free messages left: {left} of {free}.\nPlans: {prices}.",
        "id": "Sisa pesan gratis: {left} dari {free}.\nPaket: {prices}.",
        "vi": "Tin nhắn miễn phí còn lại: {left}/{free}.\nCác gói: {prices}.",
        "hi": "मुफ़्त मैसेज बाकी: {free} में से {left}.\nप्लान: {prices}.",
        "kk": "Тегін хабарлама қалды: {free} ішінен {left}.\nЖазылым: {prices}.",
        "uz": "Qolgan bepul xabarlar: {left} / {free}.\nObuna: {prices}.",
        "ja": "残りの無料メッセージ：{left} / {free}\nプラン：{prices}",
        "ko": "남은 무료 메시지: {left} / {free}\n요금제: {prices}",
        "zh": "剩余免费消息：{left} / {free}\n套餐：{prices}",
    },
    "subscribe": {
        "ru": "Подписка: {prices}. Месячная продлевается автоматически, отмена — в /status.",
        "en": "Plans: {prices}. The monthly one renews automatically; cancel in /status.",
        "id": "Paket: {prices}. Paket bulanan diperpanjang otomatis; batalkan di /status.",
        "vi": "Các gói: {prices}. Gói tháng tự động gia hạn; huỷ trong /status.",
        "hi": "प्लान: {prices}. मासिक प्लान अपने आप रिन्यू होता है; /status में रद्द करें।",
        "kk": "Жазылым: {prices}. Айлық жазылым автоматты ұзартылады; бас тарту — /status.",
        "uz": "Obuna: {prices}. Oylik obuna avtomatik uzaytiriladi; bekor qilish — /status.",
        "ja": "プラン：{prices}。1か月プランは自動更新です。解約は /status から。",
        "ko": "요금제: {prices}. 월간 요금제는 자동 갱신되며 /status 에서 취소할 수 있어요.",
        "zh": "套餐：{prices}。月度套餐自动续订，可在 /status 取消。",
    },
    "unsub_done": {
        "ru": "Продление отменено. Подписка будет работать до конца оплаченного срока.",
        "en": "Renewal cancelled. Your subscription stays active until the end of the paid period.",
        "id": "Perpanjangan dibatalkan. Langganan tetap aktif sampai akhir periode yang dibayar.",
        "vi": "Đã huỷ gia hạn. Gói vẫn hoạt động đến hết thời gian đã trả.",
        "hi": "रिन्यूअल रद्द हो गया। सब्सक्रिप्शन भुगतान की अवधि ख़त्म होने तक चलेगा।",
        "kk": "Ұзарту тоқтатылды. Жазылым төленген мерзім соңына дейін жұмыс істейді.",
        "uz": "Uzaytirish bekor qilindi. Obuna to'langan muddat oxirigacha ishlaydi.",
        "ja": "自動更新を停止しました。支払い済みの期間が終わるまでは使えます。",
        "ko": "갱신이 취소됐어요. 결제한 기간이 끝날 때까지는 계속 이용할 수 있어요.",
        "zh": "已取消续订。订阅在已付费期限结束前仍然有效。",
    },
    "thanks_paid": {
        "ru": "Спасибо! ⭐ Подписка оформлена — теперь можно общаться без ограничений.",
        "en": "Thank you! ⭐ You're subscribed — let's keep talking.",
        "id": "Terima kasih! ⭐ Langganan aktif — yuk lanjut ngobrol.",
        "vi": "Cảm ơn bạn! ⭐ Gói đã được kích hoạt — mình tiếp tục trò chuyện nhé.",
        "hi": "शुक्रिया! ⭐ सब्सक्रिप्शन चालू हो गया — चलो बातें करते हैं।",
        "kk": "Рақмет! ⭐ Жазылым рәсімделді — әңгімені жалғастырайық.",
        "uz": "Rahmat! ⭐ Obuna faollashdi — suhbatni davom ettiramiz.",
        "ja": "ありがとう！⭐ 登録できました。またおしゃべりしよう。",
        "ko": "고마워요! ⭐ 구독이 시작됐어요. 계속 이야기해요.",
        "zh": "谢谢！⭐ 订阅成功——我们继续聊吧。",
    },
    "settings": {
        "ru": "{name} иногда пишет первым, если ты долго молчишь (только при подписке, не чаще раза в день, "
              "с {from_h}:00 до {to_h}:00).",
        "en": "{name} sometimes writes first if you've been quiet for a while (subscribers only, at most once a "
              "day, between {from_h}:00 and {to_h}:00).",
        "id": "{name} kadang menyapa duluan kalau kamu lama tidak muncul (khusus pelanggan, maksimal sehari "
              "sekali, pukul {from_h}:00–{to_h}:00).",
        "vi": "{name} đôi khi sẽ nhắn trước nếu bạn im lặng lâu (chỉ với người đăng ký, tối đa một lần mỗi "
              "ngày, từ {from_h}:00 đến {to_h}:00).",
        "hi": "अगर तुम काफ़ी देर तक चुप रहो तो {name} कभी-कभी पहले मैसेज करती है (सिर्फ़ सब्सक्राइबर्स के लिए, "
              "दिन में एक बार से ज़्यादा नहीं, {from_h}:00 से {to_h}:00 तक)।",
        "kk": "Ұзақ үнсіз қалсаң, {name} кейде бірінші жазады (тек жазылымда, күніне бір реттен артық емес, "
              "{from_h}:00–{to_h}:00).",
        "uz": "Uzoq jim qolsangiz, {name} ba'zan birinchi yozadi (faqat obunada, kuniga bir martadan ko'p "
              "emas, {from_h}:00–{to_h}:00).",
        "ja": "しばらく連絡がないと、{name}からメッセージすることがあります（サブスク登録者のみ、1日1回まで、"
              "{from_h}:00〜{to_h}:00）。",
        "ko": "한동안 연락이 없으면 {name}이(가) 먼저 메시지를 보낼 때가 있어요 (구독자만, 하루 한 번까지, "
              "{from_h}:00~{to_h}:00).",
        "zh": "如果你很久没说话，{name}有时会主动找你（仅限订阅用户，每天最多一次，{from_h}:00–{to_h}:00）。",
    },
    "btn_checkins_on": {
        "ru": "Пиши мне первым", "en": "Write to me first", "id": "Sapa aku duluan", "vi": "Hãy nhắn trước cho tôi",
        "hi": "मुझे पहले मैसेज करो", "kk": "Маған бірінші жаз", "uz": "Menga birinchi yoz",
        "ja": "先に連絡してほしい", "ko": "먼저 연락해 줘요", "zh": "主动找我",
    },
    "btn_checkins_off": {
        "ru": "Не пиши первым", "en": "Don't write first", "id": "Jangan sapa duluan", "vi": "Đừng nhắn trước",
        "hi": "पहले मैसेज मत करो", "kk": "Бірінші жазба", "uz": "Birinchi yozma",
        "ja": "先に連絡しないで", "ko": "먼저 연락하지 마요", "zh": "不要主动找我",
    },
    "toggled_on": {"ru": "Включено", "en": "On", "id": "Aktif", "vi": "Đã bật", "hi": "चालू", "kk": "Қосулы",
                   "uz": "Yoqildi", "ja": "オン", "ko": "켜짐", "zh": "已开启"},
    "toggled_off": {"ru": "Выключено", "en": "Off", "id": "Nonaktif", "vi": "Đã tắt", "hi": "बंद", "kk": "Өшірулі",
                    "uz": "O'chirildi", "ja": "オフ", "ko": "꺼짐", "zh": "已关闭"},
    "forget_confirm": {
        "ru": "Стереть всю переписку и всё, что я о тебе запомнил? Это нельзя отменить.",
        "en": "Erase our whole chat and everything I remember about you? This can't be undone.",
        "id": "Hapus semua obrolan dan semua yang aku ingat tentangmu? Ini tidak bisa dibatalkan.",
        "vi": "Xoá toàn bộ cuộc trò chuyện và mọi điều mình nhớ về bạn? Không thể hoàn tác.",
        "hi": "पूरी बातचीत और तुम्हारे बारे में मुझे जो याद है, सब मिटा दूँ? यह वापस नहीं होगा।",
        "kk": "Барлық хат алмасуды және сен туралы есімдегінің бәрін өшірейін бе? Қайтару мүмкін емес.",
        "uz": "Butun yozishmani va siz haqingizda eslagan hamma narsani o'chiraymi? Buni qaytarib bo'lmaydi.",
        "ja": "これまでの会話と、あなたについて覚えていることをすべて消しますか？元に戻せません。",
        "ko": "대화 전체와 당신에 대해 기억하는 모든 것을 지울까요? 되돌릴 수 없어요.",
        "zh": "要清除全部聊天记录和我记得的关于你的一切吗？此操作无法撤销。",
    },
    "btn_yes_erase": {"ru": "Да, стереть", "en": "Yes, erase", "id": "Ya, hapus", "vi": "Có, xoá", "hi": "हाँ, मिटाओ",
                      "kk": "Иә, өшір", "uz": "Ha, o'chir", "ja": "はい、消す", "ko": "네, 지울게요", "zh": "是的，清除"},
    "btn_no": {"ru": "Нет", "en": "No", "id": "Tidak", "vi": "Không", "hi": "नहीं", "kk": "Жоқ", "uz": "Yo'q",
               "ja": "いいえ", "ko": "아니요", "zh": "不"},
    "forgot": {
        "ru": "Готово, всё стёрто. Напиши /start, чтобы познакомиться заново.",
        "en": "Done, everything is erased. Send /start to meet again.",
        "id": "Selesai, semuanya sudah dihapus. Kirim /start untuk kenalan lagi.",
        "vi": "Xong, mọi thứ đã được xoá. Gửi /start để làm quen lại.",
        "hi": "हो गया, सब मिट गया। फिर से मिलने के लिए /start भेजो।",
        "kk": "Дайын, бәрі өшірілді. Қайта танысу үшін /start жаз.",
        "uz": "Tayyor, hammasi o'chirildi. Qaytadan tanishish uchun /start yuboring.",
        "ja": "すべて消去しました。/start でまた最初から始められます。",
        "ko": "모두 지웠어요. /start 를 보내면 다시 처음부터 시작해요.",
        "zh": "已全部清除。发送 /start 重新认识吧。",
    },
    "kept": {"ru": "Ничего не трогаю 🙂", "en": "Leaving everything as is 🙂", "id": "Oke, tidak ada yang dihapus 🙂",
             "vi": "Mình giữ nguyên nhé 🙂", "hi": "ठीक है, कुछ नहीं मिटाया 🙂", "kk": "Ештеңе өшірмеймін 🙂",
             "uz": "Hech narsa o'chirilmadi 🙂", "ja": "そのままにしておくね 🙂", "ko": "그대로 둘게요 🙂",
             "zh": "好的，什么都不动 🙂"},
    "paysupport": {
        "ru": "Вопросы по оплате и возвратам: опиши проблему и приложи дату платежа.",
        "en": "Payment and refund questions: describe the problem and include the payment date.",
        "id": "Pertanyaan pembayaran dan pengembalian dana: jelaskan masalahnya dan sertakan tanggal pembayaran.",
        "vi": "Câu hỏi về thanh toán và hoàn tiền: hãy mô tả vấn đề và ghi ngày thanh toán.",
        "hi": "भुगतान और रिफ़ंड से जुड़े सवाल: समस्या बताओ और भुगतान की तारीख़ लिखो।",
        "kk": "Төлем және қайтару сұрақтары: мәселені сипаттап, төлем күнін көрсет.",
        "uz": "To'lov va qaytarish bo'yicha savollar: muammoni yozing va to'lov sanasini ko'rsating.",
        "ja": "支払い・返金についてのお問い合わせ：内容と支払い日をお知らせください。",
        "ko": "결제·환불 문의: 문제와 결제 날짜를 알려 주세요.",
        "zh": "付款和退款问题：请描述问题并附上付款日期。",
    },
    "paysupport_contact": {"ru": " Напиши {contact}.", "en": " Contact {contact}.", "id": " Hubungi {contact}.",
                           "vi": " Liên hệ {contact}.", "hi": " संपर्क करें: {contact}.", "kk": " Жаз: {contact}.",
                           "uz": " Yozing: {contact}.", "ja": " 連絡先：{contact}", "ko": " 연락처: {contact}",
                           "zh": " 联系：{contact}"},
    "terms": {
        "ru": "{name} — ИИ-собеседник для дружеского общения. Это не медицинская и не психологическая помощь. "
              "Переписка и заметки хранятся на сервере, чтобы собеседник помнил контекст; стереть их — /forget. "
              "Подписка: {prices}; месячная продлевается автоматически, отмена в /status. В подписке есть "
              "разумный дневной лимит общения. В экстренной ситуации звони в местную службу спасения.",
        "en": "{name} is an AI companion for friendly conversation. It is not medical or psychological care. "
              "Chats and notes are stored on the server so the companion remembers context; /forget erases them. "
              "Plans: {prices}; the monthly plan renews automatically, cancel in /status. Subscriptions include a "
              "reasonable daily usage limit. In an emergency, call your local emergency number.",
    },
    "help": {
        "ru": "Просто пиши — {name} ответит. Можно присылать фото.\n\n/status — подписка\n/settings — писать ли "
              "тебе первым\n/language — язык\n/forget — стереть всё\n/terms — условия\n/paysupport — помощь с оплатой",
        "en": "Just write — {name} will reply. You can send photos too.\n\n/status — subscription\n/settings — "
              "whether to write first\n/language — language\n/forget — erase everything\n/terms — terms\n"
              "/paysupport — payment help",
        "id": "Tulis saja — {name} akan membalas. Bisa kirim foto juga.\n\n/status — langganan\n/settings — sapaan "
              "duluan\n/language — bahasa\n/forget — hapus semua\n/terms — ketentuan\n/paysupport — bantuan pembayaran",
        "vi": "Cứ nhắn — {name} sẽ trả lời. Bạn cũng có thể gửi ảnh.\n\n/status — gói đăng ký\n/settings — nhắn "
              "trước\n/language — ngôn ngữ\n/forget — xoá tất cả\n/terms — điều khoản\n/paysupport — hỗ trợ thanh toán",
        "hi": "बस लिखो — {name} जवाब देगी। फ़ोटो भी भेज सकते हो।\n\n/status — सब्सक्रिप्शन\n/settings — पहले "
              "मैसेज करना\n/language — भाषा\n/forget — सब मिटाओ\n/terms — शर्तें\n/paysupport — भुगतान में मदद",
        "kk": "Жай жаза бер — {name} жауап береді. Сурет те жіберуге болады.\n\n/status — жазылым\n/settings — "
              "бірінші жазу\n/language — тіл\n/forget — бәрін өшіру\n/terms — шарттар\n/paysupport — төлем көмегі",
        "uz": "Shunchaki yozing — {name} javob beradi. Rasm ham yuborishingiz mumkin.\n\n/status — obuna\n"
              "/settings — birinchi yozish\n/language — til\n/forget — hammasini o'chirish\n/terms — shartlar\n"
              "/paysupport — to'lov bo'yicha yordam",
        "ja": "気軽にメッセージしてね — {name}が返事します。写真も送れます。\n\n/status — サブスク\n/settings — "
              "こちらからの連絡\n/language — 言語\n/forget — すべて消去\n/terms — 規約\n/paysupport — 支払いのサポート",
        "ko": "편하게 메시지를 보내 주세요 — {name}이(가) 답할게요. 사진도 보낼 수 있어요.\n\n/status — 구독\n"
              "/settings — 먼저 연락하기\n/language — 언어\n/forget — 모두 지우기\n/terms — 약관\n/paysupport — 결제 도움",
        "zh": "直接发消息——{name}会回复你。也可以发照片。\n\n/status — 订阅\n/settings — 主动问候\n"
              "/language — 语言\n/forget — 全部清除\n/terms — 条款\n/paysupport — 付款帮助",
    },
    "voice": {
        "ru": "Голосовые пока не слушаю 🙈 Напиши, пожалуйста, текстом.",
        "en": "I can't listen to voice messages yet 🙈 Could you type it?",
        "id": "Aku belum bisa dengar pesan suara 🙈 Tolong ketik ya.",
        "vi": "Mình chưa nghe được tin nhắn thoại 🙈 Bạn nhắn chữ giúp mình nhé.",
        "hi": "मैं अभी वॉइस मैसेज नहीं सुन पाती 🙈 प्लीज़ लिखकर भेजो।",
        "kk": "Дауыстық хабарламаларды әзірге тыңдай алмаймын 🙈 Жазып жіберші.",
        "uz": "Ovozli xabarlarni hozircha eshita olmayman 🙈 Iltimos, yozib yuboring.",
        "ja": "ボイスメッセージはまだ聞けないの 🙈 文字で送ってくれる？",
        "ko": "아직 음성 메시지는 못 들어요 🙈 글로 보내 줄래요?",
        "zh": "我暂时还听不了语音 🙈 可以打字发给我吗？",
    },
    "bad_file": {
        "ru": "Я пока понимаю только текст, фото и стикеры 🙂", "en": "For now I understand text, photos and stickers 🙂",
        "id": "Untuk sekarang aku cuma paham teks, foto, dan stiker 🙂",
        "vi": "Hiện mình chỉ hiểu chữ, ảnh và sticker thôi 🙂", "hi": "अभी मैं सिर्फ़ टेक्स्ट, फ़ोटो और स्टिकर समझती हूँ 🙂",
        "kk": "Әзірге мәтін, сурет және стикерді ғана түсінемін 🙂",
        "uz": "Hozircha faqat matn, rasm va stikerlarni tushunaman 🙂",
        "ja": "今はテキスト、写真、スタンプだけわかるよ 🙂", "ko": "지금은 글, 사진, 스티커만 알아볼 수 있어요 🙂",
        "zh": "我目前只能看懂文字、照片和贴纸 🙂",
    },
    "big_file": {"ru": "Файл слишком большой, Telegram не даёт мне его открыть 😔",
                 "en": "That file is too big — Telegram won't let me open it 😔"},
    "busy": {
        "ru": "Ой, что-то я подвисаю… напиши ещё раз через минутку 🙏",
        "en": "Oops, I'm a bit overloaded… try again in a minute 🙏",
        "id": "Aduh, aku lagi agak lambat… coba lagi sebentar ya 🙏",
        "vi": "Ôi, mình đang hơi chậm… thử lại sau một phút nhé 🙏",
        "hi": "उफ़, मैं थोड़ा अटक रही हूँ… एक मिनट बाद फिर लिखो 🙏",
        "kk": "Ой, сәл тұрып қалдым… бір минуттан кейін қайта жазшы 🙏",
        "uz": "Voy, biroz qotib qoldim… bir daqiqadan keyin yana yozing 🙏",
        "ja": "ごめん、ちょっと調子が悪いみたい…1分後にもう一度送ってね 🙏",
        "ko": "앗, 잠깐 버벅이네요… 1분 뒤에 다시 보내 주세요 🙏",
        "zh": "哎呀，我有点卡……过一分钟再发一次吧 🙏",
    },
    "api_error": {
        "ru": "Что-то со связью у меня… напиши ещё раз, пожалуйста.",
        "en": "Something's wrong with my connection… please send that again.",
        "id": "Koneksiku lagi bermasalah… tolong kirim lagi ya.",
        "vi": "Kết nối của mình có vấn đề… bạn gửi lại giúp mình nhé.",
        "hi": "मेरे कनेक्शन में कुछ गड़बड़ है… प्लीज़ फिर से भेजो।",
        "kk": "Байланысымда бірдеңе болды… қайта жазшы.",
        "uz": "Aloqamda nimadir bo'ldi… iltimos, yana yuboring.",
        "ja": "接続がうまくいかないみたい…もう一度送ってくれる？",
        "ko": "연결에 문제가 있어요… 한 번만 다시 보내 줄래요?",
        "zh": "我这边网络有点问题……请再发一次。",
    },
    "limit_day": {
        "ru": "Мы сегодня очень много проговорили 🙂 Дневной лимит общения закончился — давай продолжим завтра, я буду ждать.",
        "en": "We've talked a lot today 🙂 Today's limit is reached — let's continue tomorrow, I'll be here.",
        "id": "Hari ini kita sudah ngobrol banyak banget 🙂 Batas harian sudah tercapai — lanjut besok ya, aku tunggu.",
        "vi": "Hôm nay mình đã trò chuyện rất nhiều rồi 🙂 Đã hết giới hạn trong ngày — mai mình nói tiếp nhé.",
        "hi": "आज हमने बहुत बातें कीं 🙂 आज की सीमा पूरी हो गई — कल बात करते हैं, मैं इंतज़ार करूँगी।",
        "kk": "Бүгін көп сөйлестік 🙂 Күндік лимит бітті — ертең жалғастырайық, күтемін.",
        "uz": "Bugun juda ko'p gaplashdik 🙂 Kunlik limit tugadi — ertaga davom etamiz, kutaman.",
        "ja": "今日はたくさん話したね 🙂 今日の上限に達しました。また明日話そう、待ってるね。",
        "ko": "오늘 정말 많이 이야기했네요 🙂 오늘 한도에 도달했어요. 내일 또 이야기해요, 기다릴게요.",
        "zh": "今天我们聊了好多 🙂 今天的额度用完了——明天继续吧，我等你。",
    },
    "limit_period": {
        "ru": "Лимит общения на этот оплаченный срок закончился. Он обновится {date}.",
        "en": "The usage limit for this paid period is reached. It resets on {date}.",
        "id": "Batas penggunaan untuk periode ini sudah tercapai. Akan diperbarui pada {date}.",
        "vi": "Đã hết giới hạn sử dụng của kỳ này. Giới hạn sẽ được làm mới vào {date}.",
        "hi": "इस अवधि की सीमा पूरी हो गई। यह {date} को फिर से शुरू होगी।",
        "kk": "Осы төленген мерзімдегі лимит бітті. Ол {date} жаңарады.",
        "uz": "Ushbu to'langan muddat uchun limit tugadi. U {date} yangilanadi.",
        "ja": "この期間の上限に達しました。{date}にリセットされます。",
        "ko": "이번 결제 기간의 한도에 도달했어요. {date}에 초기화돼요.",
        "zh": "本付费周期的额度已用完，将于 {date} 重置。",
    },
    "refusal": {
        "ru": "Давай об этом не будем 🙂 Расскажи лучше, как ты?",
        "en": "Let's not go there 🙂 Tell me how you're doing instead?",
        "id": "Kita bahas yang lain aja ya 🙂 Kamu sendiri gimana kabarnya?",
        "vi": "Mình nói chuyện khác nhé 🙂 Dạo này bạn thế nào?",
        "hi": "चलो इस बारे में बात नहीं करते 🙂 बताओ, तुम कैसे हो?",
        "kk": "Бұл туралы айтпай-ақ қояйық 🙂 Өзің қалайсың?",
        "uz": "Bu haqda gapirmay qo'yaylik 🙂 O'zingiz qalaysiz?",
        "ja": "その話はやめておこう 🙂 それより最近どう？",
        "ko": "그 얘기는 하지 말아요 🙂 요즘 어떻게 지내요?",
        "zh": "这个我们就不聊了吧 🙂 说说你最近怎么样？",
    },
    "language_pick": {"ru": "Выбери язык:", "en": "Choose a language:", "id": "Pilih bahasa:", "vi": "Chọn ngôn ngữ:",
                      "hi": "भाषा चुनो:", "kk": "Тілді таңда:", "uz": "Tilni tanlang:", "ja": "言語を選んでね：",
                      "ko": "언어를 골라 주세요:", "zh": "选择语言："},
    "language_set": {"ru": "Готово, говорим по-русски 🙂", "en": "Done — English it is 🙂",
                     "id": "Oke, kita pakai Bahasa Indonesia 🙂", "vi": "Xong, mình nói tiếng Việt nhé 🙂",
                     "hi": "ठीक है, अब हिन्दी में बात करेंगे 🙂", "kk": "Дайын, қазақша сөйлесеміз 🙂",
                     "uz": "Tayyor, o'zbekcha gaplashamiz 🙂", "ja": "OK、日本語で話そう 🙂", "ko": "좋아요, 한국어로 이야기해요 🙂",
                     "zh": "好的，我们用中文聊 🙂"},
    # LINE (no in-chat payments yet, and text commands instead of bot commands).
    "line_paywall": {
        "ru": "Бесплатные сообщения закончились. Подписка в LINE скоро появится — я напишу, как только можно будет продолжить.",
        "en": "Your free messages are used up. Subscriptions on LINE are coming soon — I'll let you know as soon as we can continue.",
        "ja": "無料メッセージを使い切りました。LINEでのサブスクリプションはもうすぐ始まります。続けられるようになったらお知らせするね。",
        "ko": "무료 메시지를 모두 사용했어요. LINE 구독은 곧 시작돼요. 다시 이야기할 수 있게 되면 알려 줄게요.",
        "zh": "免费消息已用完。LINE 订阅即将上线——可以继续聊天时我会告诉你。",
    },
    "line_trial": {
        "ru": "Бесплатных сообщений осталось: {left} из {free}. Подписка в LINE скоро появится.",
        "en": "Free messages left: {left} of {free}. Subscriptions on LINE are coming soon.",
        "ja": "残りの無料メッセージ：{left} / {free}。LINEでのサブスクリプションはもうすぐ始まります。",
        "ko": "남은 무료 메시지: {left} / {free}. LINE 구독은 곧 시작돼요.",
        "zh": "剩余免费消息：{left} / {free}。LINE 订阅即将上线。",
    },
    "line_help": {
        "ru": "Просто пиши — {name} ответит. Можно присылать фото.\n\n/status — сообщения и подписка\n/settings — писать ли "
              "тебе первым\n/language — язык\n/forget — стереть всё",
        "en": "Just write — {name} will reply. You can send photos too.\n\n/status — messages and subscription\n"
              "/settings — whether to write first\n/language — language\n/forget — erase everything",
        "ja": "気軽にメッセージしてね — {name}が返事します。写真も送れます。\n\n/status — メッセージとサブスク\n"
              "/settings — こちらからの連絡\n/language — 言語\n/forget — すべて消去",
        "ko": "편하게 메시지를 보내 주세요 — {name}이(가) 답할게요. 사진도 보낼 수 있어요.\n\n/status — 메시지와 구독\n"
              "/settings — 먼저 연락하기\n/language — 언어\n/forget — 모두 지우기",
        "zh": "直接发消息——{name}会回复你。也可以发照片。\n\n/status — 消息和订阅\n/settings — 主动问候\n"
              "/language — 语言\n/forget — 全部清除",
    },
    # YooKassa is offered to Russian-speaking users only.
    "pay_link": {"ru": "Подписка на {days} дней — {rub} ₽. Чек придёт на {email}.{consent}\n\nПосле оплаты вернись сюда — "
                       "я сам замечу платёж в течение минуты, или нажми «Я оплатил»."},
    "pay_consent": {"ru": " Оплачивая, ты соглашаешься на автопродление: каждые {days} дней с карты будет списываться "
                          "{rub} ₽. Отключить можно в /status."},
    "btn_go_pay": {"ru": "💳 Перейти к оплате"},
    "btn_paid": {"ru": "✅ Я оплатил"},
    "pay_failed": {"ru": "Не получилось создать платёж 😔 Попробуй чуть позже или оплати звёздами."},
    "ask_email": {"ru": "Пришли, пожалуйста, email — на него придёт чек об оплате (так требует закон)."},
    "paid_ok": {"ru": "Оплата прошла!"},
    "already_active": {"ru": "Подписка уже активна 🙂", "en": "Your subscription is already active 🙂"},
    "not_paid_yet": {"ru": "Пока не вижу оплату. Если уже оплатил — подожди минутку."},
}


def t(lang: str, key: str, **kw) -> str:
    texts = STRINGS[key]
    text = texts.get(lang) or texts.get(DEFAULT) or texts["ru"]
    return text.format(**kw)
