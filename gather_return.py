"""Schedule each resource march's travel, gathering, and return separately."""
from dataclasses import dataclass
from functools import lru_cache
import re


@dataclass
class MarchRow:
    icon: object
    phase: str
    seconds: int


@dataclass
class GatherQueue:
    number: int
    icon: object
    phase: str
    due: float
    return_total: int = 0
    missing: bool = False
    check_at: float = 0


class GatherTask:
    retry_seconds = 60
    confirm_seconds = 5

    def __init__(self, bot, serial, templates, **options):
        self.bot, self.serial, self.templates = bot, serial, templates
        self.options = options
        self.icons = []
        self.queues = []
        self.next_number = 1
        self.next_check_at = 0
        self.first = True
        self.image = None
        self.header = None

    def rows(self, image):
        if image is None:
            return None
        bot = self.bot
        lines = bot.recognize_text(image.crop((0, 430, 395, 1250)))
        compact = [(text.replace(' ', ''), box) for text, box in lines]
        capacity = next((re.search(r'(\d+)[/／](\d+)', text)
                         for text, _ in compact if re.search(r'(\d+)[/／](\d+)', text)), None)
        if capacity is None:
            # The entire march drawer can disappear when the last march returns.
            uncertain = any(('行军' in text and '增加行军队列' not in text)
                            or bot.parse_countdown(text) is not None for text, _ in compact)
            # Confirm the drawer itself vanished; an OCR miss is not a free queue.
            header_gone = (self.header is not None and
                           bot.difference(image.crop((10, 455, 360, 515)).convert('RGB'), self.header) > 25)
            return [] if not uncertain and header_gone else None
        count = int(capacity[1])
        bottom = min((box[1] for text, box in compact if '增加行军队列' in text), default=820)
        clocks = sorted(((box, bot.parse_countdown(text)) for text, box in compact
                         if box[1] < bottom and bot.parse_countdown(text) is not None), key=lambda item: item[0][1])
        if len(clocks) != count:
            return None
        self.header = image.crop((10, 455, 360, 515)).convert('RGB')
        rows = []
        previous_bottom = 0
        for (_, y, _, height), seconds in clocks:
            center = 430 + y + height / 2 - 16
            icon = image.crop((24, round(center - 24), 78, round(center + 24))).convert('RGB')
            labels = ''.join(text for text, box in compact if previous_bottom < box[1] < y)
            phase = ('returning' if any(word in labels for word in ('返回', '返程')) else
                     'gathering' if '采集中' in labels else
                     'outbound' if re.search(r'[（(]\d+[,，]\d+[)）]', labels) or '行军中' in labels else
                     'unknown')
            rows.append(MarchRow(icon, phase, seconds))
            previous_bottom = y + height
        return rows

    def known(self, icon):
        return any(self.bot.difference(icon, reference) < 18 for reference in self.icons)

    def learn(self, rows):
        for row in rows:
            if row.phase == 'gathering' and not self.known(row.icon):
                self.icons.append(row.icon)

    def _schedule(self, now, retry=None):
        # Only a timer expiry (or a failed recognition) requests another screenshot.
        if retry is not None:
            for queue in self.queues:
                if queue.check_at <= now:
                    queue.check_at = now + retry
        deadlines = [queue.check_at if queue.check_at > now else now + self.confirm_seconds
                     for queue in self.queues]
        if retry is not None:
            deadlines.append(now + retry)
        self.next_check_at = min(deadlines, default=now + self.retry_seconds)

    def _sync(self, rows, now):
        """Match queues individually; duplicate resources may finish at different times."""
        self.learn(rows)
        candidates = [[] for _ in self.queues]
        transitions = {'outbound': ('outbound', 'gathering', 'returning'),
                       'gathering': ('gathering', 'returning'),
                       'returning': ('returning',), 'unknown': ('outbound', 'gathering', 'returning')}
        for qi, queue in enumerate(self.queues):
            for ri, row in enumerate(rows):
                difference = self.bot.difference(queue.icon, row.icon)
                if difference >= 18 or row.phase not in (*transitions[queue.phase], 'unknown'):
                    continue
                # Countdown agreement takes precedence over row order for repeated resources.
                remaining = max(0, queue.due - now)
                cost = (abs(row.seconds - remaining) if queue.phase == row.phase else 100000) + difference
                candidates[qi].append((ri, cost))

        @lru_cache(None)
        def assign(qi, used):
            if qi == len(self.queues):
                return 0, 0, ()
            best = assign(qi + 1, used)
            for ri, cost in candidates[qi]:
                if used & (1 << ri):
                    continue
                count, remaining_cost, pairs = assign(qi + 1, used | (1 << ri))
                option = count + 1, remaining_cost + cost, ((qi, ri),) + pairs
                if (option[0], -option[1]) > (best[0], -best[1]):
                    best = option
            return best

        # The march list has at most six rows. A global match prevents a close
        # duplicate timer stealing another queue's identity after rows reorder.
        matched = dict(assign(0, 0)[2])
        used = set(matched.values())
        returned = False
        keep = []
        for qi, queue in enumerate(self.queues):
            if qi in matched:
                row = rows[matched[qi]]
                queue.missing = False
                if row.phase == 'unknown':
                    queue.check_at = queue.due if queue.due > now else now + self.retry_seconds
                else:
                    self._record(queue, row, now, len(rows))
                keep.append(queue)
            elif queue.phase == 'returning' and now >= queue.due and len(rows) < queue.return_total:
                # Only a previously observed return countdown can reach this branch.
                if queue.missing:
                    returned = True
                    self.bot.print(f'已确认采集队列 {queue.number} 返程结束且行军队列减少，按配置顺序重新采集', flush=True)
                else:
                    queue.missing = True
                    queue.check_at = now + self.confirm_seconds
                    keep.append(queue)
            else:
                # Never infer a return countdown from another march or disappearance.
                keep.append(queue)
                queue.check_at = queue.due if queue.due > now else now + self.retry_seconds
        self.queues = keep
        for ri, row in enumerate(rows):
            if ri not in used and self.known(row.icon) and row.phase != 'unknown':
                queue = GatherQueue(self.next_number, row.icon, 'unknown', now)
                self.next_number += 1
                self._record(queue, row, now, len(rows))
                self.queues.append(queue)
        self._schedule(now)
        return returned

    def _record(self, queue, row, now, total):
        changed = queue.phase != row.phase
        queue.icon = row.icon
        queue.phase = row.phase
        queue.due = now + max(1, row.seconds)
        queue.check_at = queue.due
        if row.phase == 'returning':
            queue.return_total = total
        if changed:
            label = {'outbound': '前往资源点', 'gathering': '采集中', 'returning': '返回中'}[row.phase]
            self.bot.print(f'采集队列 {queue.number}：{label}，记录倒计时 {row.seconds} 秒，到期后复查', flush=True)

    def dispatched(self, before, after):
        now = self.bot.time.monotonic()
        previous, current = self.rows(before), self.rows(after)
        if current is None:
            self._schedule(now, self.retry_seconds)
            return
        if previous is not None:
            unmatched = list(current)
            for row in previous:
                match = next((i for i, other in enumerate(unmatched)
                              if self.bot.difference(row.icon, other.icon) < 18), None)
                if match is not None:
                    unmatched.pop(match)
            if len(current) == len(previous) + 1 and len(unmatched) == 1:
                if not self.known(unmatched[0].icon):
                    self.icons.append(unmatched[0].icon)
        self._sync(current, now)

    def ready(self):
        self.image = None
        if self.first:
            self.first = False
            return True
        bot = self.bot
        if bot.time.monotonic() < self.next_check_at:
            return False
        self.image = bot.enter_wilderness(self.serial, bot.task_screen(self.serial),
                                          self.templates[0], self.templates[1], '采集队列倒计时到期检查')
        now = bot.time.monotonic()
        rows = self.rows(self.image)
        if rows is None:
            for queue in self.queues:
                queue.missing = False
            self._schedule(now, self.retry_seconds)
            return False
        return self._sync(rows, now)

    def __call__(self):
        image = self.bot.run_gather(self.serial, *self.templates,
                                    return_monitor=self, **self.options)
        now = self.bot.time.monotonic()
        rows = self.rows(image)
        if rows is not None:
            self._sync(rows, now)
        else:
            self._schedule(now, self.retry_seconds)
        return image
