"""Schedule each resource march's travel, gathering, and return separately."""
from dataclasses import dataclass
from functools import lru_cache
import re
from PIL import ImageChops, ImageStat
from screen_geometry import scene_view


@dataclass
class MarchRow:
    icon: object
    phase: str
    seconds: int
    resource: str | None = None


@dataclass
class GatherQueue:
    number: int
    icon: object
    phase: str
    due: float
    return_total: int = 0
    missing: bool = False
    check_at: float = 0
    step: int | None = None


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
        self.header_box = (10, 455, 360, 515)
        steps = options.get('steps')
        if steps is None:
            steps = [(resource, options.get('levels', {}).get(resource, 1))
                     for resource in options.get('resources', tuple(bot.GATHER_RESOURCES))]
        self.steps = list(steps)
        self.pending = list(range(len(self.steps)))
        self.unconfirmed = []
        self.started = False
        self.other_rows = []
        self.bootstrap = True
        self.resource_icons = {
            resource: bot.open_template(bot.ROOT / f'assets/emulator-gather-march-{resource}.png').convert('RGB')
            for resource in bot.GATHER_RESOURCES}

    def available_steps(self, image):
        self.started = True
        rows = self.rows(image)
        if rows is None:
            raise self.bot.QueueCapacityUnavailable('未完整识别采集队列，暂不补派')
        self._sync(rows, self.bot.time.monotonic())
        if self.bootstrap and self.pending and any(row.phase == 'returning' for row in self.other_rows):
            # A startup return flag has lost its resource identity. Let it
            # finish before filling missing items; never count it as mining.
            return []
        self.bootstrap = False
        return [(index, self.steps[index]) for index in sorted(self.pending)]

    def is_pending(self, index):
        return index in self.pending

    def rows(self, image):
        if image is None:
            return None
        bot = self.bot
        original = image
        # March icons must retain their shape when the HUD is stretched for
        # different screen sizes or list lengths.
        view = scene_view(image)
        if view is not None:
            image = view[0]
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
                           bot.difference(image.crop(self.header_box).convert('RGB'), self.header) > 25)
            empty = (not uncertain and bot.town_button(original) and
                     any('城镇' in text for text, _ in bot.recognize_text(
                         original.crop((900, 2200, 1080, 2340)))))
            return [] if not uncertain and (header_gone or empty) else None
        count = int(capacity[1])
        bottom = min((box[1] for text, box in compact if '增加行军队列' in text), default=820)
        clocks = sorted(((box, bot.parse_countdown(text)) for text, box in compact
                         if box[1] < bottom and bot.parse_countdown(text) is not None), key=lambda item: item[0][1])
        if len(clocks) != count:
            return None
        _, (_, cy, _, ch) = next((text, box) for text, box in compact
                                 if re.search(r'(\d+)[/／](\d+)', text))
        self.header_box = (10, round(430 + cy - 12), 360, round(430 + cy + ch + 12))
        self.header = image.crop(self.header_box).convert('RGB')
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
            scores = []
            for offset in range(-6, 7, 2):
                candidate = image.crop((24, round(center - 24) + offset,
                                        78, round(center + 24) + offset)).convert('RGB')
                for resource, reference in self.resource_icons.items():
                    score = sum(ImageStat.Stat(ImageChops.difference(candidate, reference)).mean) / 3
                    scores.append((score, resource, offset, candidate))
            score, resource, _, matched_icon = min(scores, key=lambda item: item[0])
            rows.append(MarchRow(matched_icon if score < 18 else icon, phase, seconds,
                                 resource if score < 18 else None))
            previous_bottom = y + height
        return rows

    def known(self, icon):
        return any(self.bot.difference(icon, reference) < 18 for reference in self.icons)

    def learn(self, rows):
        for row in rows:
            if (row.phase == 'gathering' or row.resource is not None) and not self.known(row.icon):
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
        if self.started and self.pending:
            deadlines.append(now + self.retry_seconds)
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
                if (queue.step is not None and row.resource is not None
                        and self.steps[queue.step][0] != row.resource):
                    continue
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
        # Returning marches replace the resource picture with a blue flag.
        # Account for unrelated marches first; only an unambiguous new return
        # may inherit a missing gathering queue's configured identity.
        other_used = set()
        for previous in self.other_rows:
            choices = [(abs(row.seconds - previous.seconds), ri) for ri, row in enumerate(rows)
                       if ri not in used and ri not in other_used
                       and self.bot.difference(previous.icon, row.icon) < 18]
            if choices:
                other_used.add(min(choices)[1])
        missing = [qi for qi, queue in enumerate(self.queues)
                   if qi not in matched and queue.phase in ('gathering', 'outbound')]
        returns = [ri for ri, row in enumerate(rows)
                   if ri not in used and ri not in other_used and row.phase == 'returning']
        if missing and len(missing) == len(returns) and len(other_used) == len(self.other_rows):
            matched.update(zip(missing, returns))
            used.update(returns)
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
            elif queue.phase == 'returning' and now >= queue.due:
                # Only a previously observed return countdown can reach this branch.
                if queue.missing:
                    if now < queue.check_at:
                        keep.append(queue)
                        continue
                    if queue.step is not None:
                        self.pending.append(queue.step)
                        returned = True
                    self.bot.print(f'已确认采集队列 {queue.number} 返程结束且资源行消失，补派对应配置', flush=True)
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
                if self.unconfirmed:
                    queue.step = self.unconfirmed.pop(0)
                elif self.pending:
                    index = next((index for index in self.pending
                                  if row.resource is None or self.steps[index][0] == row.resource), None)
                    if index is not None:
                        queue.step = index
                        self.pending.remove(index)
                self.next_number += 1
                self._record(queue, row, now, len(rows))
                self.queues.append(queue)
                used.add(ri)
        self.other_rows = [row for ri, row in enumerate(rows) if ri not in used]
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

    def dispatched(self, before, after, step=None):
        now = self.bot.time.monotonic()
        # Reserve the configured item immediately after a confirmed dispatch.
        # A delayed drawer/OCR must never cause a retry to send it twice.
        if step is not None and step in self.pending:
            self.pending.remove(step)
            self.unconfirmed.append(step)
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
                if step is not None:
                    self.unconfirmed.remove(step)
                    queue = GatherQueue(self.next_number, unmatched[0].icon, 'unknown', now, step=step)
                    self.next_number += 1
                    if unmatched[0].phase != 'unknown':
                        self._record(queue, unmatched[0], now, len(current))
                    else:
                        queue.check_at = now + self.retry_seconds
                    self.queues.append(queue)
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
        returned = self._sync(rows, now)
        return returned or (self.started and bool(self.pending))

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
