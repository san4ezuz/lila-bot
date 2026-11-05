-- Четыре MVP-метрики из docs/PLAN.md §12 / §9. Запуск:
--   psql "$DATABASE_URL" -f scripts/metrics.sql
-- Каждый запрос — отдельный блок; psql печатает их последовательно одним прогоном.
--
-- Все события из §12 плана теперь логируются (response_skipped, game_abandoned, feedback_given
-- добавлены отдельными коммитами после Этапа 9 — см. scripts/abandon_stale_games.py и
-- POST /game/{id}/skip, /game/{id}/feedback). feedback_given хранит сырой текст ответа
-- (events.props->>'text') — если понадобится структурировать (напр. sentiment), это уже
-- отдельная задача поверх сырых данных, не блокирует базовый сбор.

-- 1. % партий, дошедших до финиша, от общего числа начатых партий.
--    "Начата" = есть событие game_started; финиш = games.status = 'finished'
--    (включает оба finish_reason — 'enlightenment' и 'turn_limit', это раздел 4 ниже).
select
    count(*) filter (where g.status = 'finished')                      as finished_games,
    count(*)                                                           as started_games,
    round(
        100.0 * count(*) filter (where g.status = 'finished') / nullif(count(*), 0),
        1
    ) as pct_finished
from games g;

-- 2. % пользователей, сыгравших бесплатную партию, которые сконвертировались во вторую
--    (платную) партию — считаем по факту наличия хотя бы одной is_paid=true партии,
--    а не по балансу credit_ledger (который может быть > 0 без реального повторного старта).
with free_players as (
    select distinct user_id
    from games
    where is_paid = false
),
converted as (
    select distinct user_id
    from games
    where is_paid = true
)
select
    (select count(*) from free_players)                                     as free_players,
    (select count(*) from converted)                                        as converted_players,
    round(
        100.0 * (select count(*) from converted)
        / nullif((select count(*) from free_players), 0),
        1
    ) as pct_converted;

-- 3. Средняя длина ответа игрока на клетку (символы), прокси глубины вовлечения.
select
    count(*)                                    as player_messages,
    round(avg(char_length(content)), 1)         as avg_chars,
    round(avg(array_length(regexp_split_to_array(trim(content), '\s+'), 1)), 1) as avg_words
from messages
where role = 'player';

-- 4. Распределение причин завершения партии.
select
    coalesce(finish_reason, 'not_finished') as finish_reason,
    count(*)                                as games,
    round(100.0 * count(*) / sum(count(*)) over (), 1) as pct
from games
where status = 'finished'
group by finish_reason
order by games desc;

-- Бонус (не входит в 4 основные метрики, но дёшево посчитать заодно): воронка пейволла.
select
    count(*) filter (where name = 'paywall_shown')     as paywall_shown,
    count(*) filter (where name = 'payment_succeeded') as payment_succeeded,
    round(
        100.0 * count(*) filter (where name = 'payment_succeeded')
        / nullif(count(*) filter (where name = 'paywall_shown'), 0),
        1
    ) as pct_paywall_to_payment
from events
where name in ('paywall_shown', 'payment_succeeded');
