"""One bounded Nox collection pass, with actual frames backing every new crop.

Only existing task actions are used. Unseen templates remain pending rather
than being synthesized, renamed, or taken from a different Android device.
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from PIL import Image
import help_bot as bot
from device_lease import DeviceLease
from emulator_runtime import avd_key, nox_endpoints
from image_matching import _template
from screen_geometry import _landmark


def main():
    bot.configure_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True)
    parser.add_argument("--gather-level", type=int, choices=range(1, 9), default=1)
    parser.add_argument("--train-units", nargs="+", choices=tuple(bot.UNIT_TYPES), default=list(bot.UNIT_TYPES))
    parser.add_argument("--intelligence-limit", type=int, default=2,
                        help="Successful ordinary missions per verification pass; 0 checks all")
    parser.add_argument("--tasks", nargs="+", default=["donate", "recruit_advanced", "recruit_epic",
                        "warehouse", "stamina", "tree", "dawn", "treasure", "explore",
                        "train", *bot.PET_SKILLS, "task_rewards", "heal", "hunt", "intelligence",
                        "bounty", "gather", "bear_drive", "bear_follow", "bear_follow_no_heroes",
                        "help_clicker", "monitor"])
    args = parser.parse_args()
    if args.intelligence_limit < 0:
        parser.error("Intelligence limit cannot be negative")
    root = bot.ROOT / "tests/fixtures/nox" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    root.mkdir(parents=True, exist_ok=True)
    provenance = bot.ROOT / "assets/nox-samples.json"
    samples = json.loads(provenance.read_text(encoding="utf-8")) if provenance.exists() else {}
    references = {p.stem.removeprefix("emulator-"): Image.open(p).convert("RGB")
                  for p in sorted((bot.ROOT / "assets").glob("emulator-*.png"))}
    original = bot.task_screen
    original_event = bot.emit_task_event

    def task_event(**event):
        if event.get("reason"):
            print(f"STATE {event.get('name')}: {event.get('status')} {event['reason']}", flush=True)
        return original_event(**event)

    bot.emit_task_event = task_event
    original_tap = bot.task_tap

    def verified_tap(serial, x, y):
        print(f"ACTION tap {x},{y}; native={bot.screen_adapter.geometry(serial).point(x, y)}", flush=True)
        return original_tap(serial, x, y)

    bot.task_tap = verified_tap
    state = {"task": "initial", "frame": 0, "last": None}

    def sample(image, force=False):
        pixels = np.asarray(image.convert("L"))
        small = cv2.resize(pixels, (270, 585), interpolation=cv2.INTER_AREA)
        # Loading animation frames are still sampled for matching, but store
        # only materially different frames to keep the regression set bounded.
        changed = state["last"] is None or np.abs(pixels.astype(np.int16) - state["last"]).mean() > 1
        matches = []
        for name, reference in references.items():
            if name in samples:
                continue
            if name.startswith("can-") and (state["task"] != "stamina"
                    or (name == "can-button" and not bot.stamina_page(image))):
                continue
            target = np.asarray(reference.convert("L"))
            if target.std() < 12:
                continue
            coarse = cv2.resize(target, (max(1, round(reference.width / 4)),
                                         max(1, round(reference.height / 4))), interpolation=cv2.INTER_AREA)
            _, confidence, _, (cx, cy) = cv2.minMaxLoc(cv2.matchTemplate(small, coarse, cv2.TM_CCOEFF_NORMED))
            if confidence < .75:
                continue
            left, top = max(0, cx * 4 - 12), max(0, cy * 4 - 12)
            region = pixels[top:min(2340, cy * 4 + reference.height + 16),
                            left:min(1080, cx * 4 + reference.width + 16)]
            if region.shape[0] < target.shape[0] or region.shape[1] < target.shape[1]:
                continue
            scores = cv2.matchTemplate(region, target, cv2.TM_CCOEFF_NORMED)
            _, score, _, (x, y) = cv2.minMaxLoc(scores)
            x, y = x + left, y + top
            if score < .96:
                continue
            box = (x, y, x + reference.width, y + reference.height)
            crop = image.crop(box).convert("RGB")
            if np.abs(np.asarray(crop.convert("L")).astype(np.int16) - target).mean() > 12:
                continue
            matches.append((name, box, crop))
        if not (changed or force or matches):
            return image
        state["frame"] += 1
        name = f"tour-{state['task']}-{state['frame']:03}"
        image.save(root / f"{name}.png")
        image.info["native_frame"].save(root / f"{name}-native.png")
        state["last"] = pixels.astype(np.int16)
        for asset, box, crop in matches:
            crop.save(bot.ROOT / f"assets/emulator-{asset}.png")
            relative = root.relative_to(bot.ROOT).as_posix()
            samples[asset] = {"native": f"{relative}/{name}-native.png",
                              "normalized": f"{relative}/{name}.png", "box": box,
                              "device": args.device, "model": model}
            print(f"NOX SAMPLE {asset}: {name} {box}", flush=True)
        if matches:
            provenance.write_text(json.dumps(samples, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            _template.cache_clear()
            _landmark.cache_clear()
        print(f"FRAME {name}; captured templates {len(samples)}/{len(references)}", flush=True)
        return image

    def capture(serial, **kwargs):
        return sample(original(serial, **kwargs))

    def asset(name, mode="L"):
        return bot.open_template(bot.ROOT / f"assets/emulator-{name}.png").convert(mode)

    for variable, name in (("CHAT_TEMPLATE", "chat-title"), ("TOWN_TEMPLATE", "town-button"),
                           ("RECONNECT_PROMO_CLOSE", "reconnect-promo-close"),
                           ("RECONNECT_WELCOME_CLOSE", "reconnect-welcome-close"),
                           ("ACTIVITY_CLOSE", "activity-close")):
        setattr(bot, variable, asset(name))
    help_icon, nav, offline, reconnect = [asset(n) for n in ("help-icon", "city-nav", "forced-text", "reconnect")]
    drawer, warehouse, tree = [asset(n, "RGB") for n in ("drawer-open", "warehouse-title", "tree-collect-label")]
    island = asset("island-title")
    templates = {n: asset(n, "RGB") for n in (*bot.TASK_BOXES, "donate-label", "free-label", "thumb")}
    timers = {}

    def verify_intelligence():
        original_execute, original_target = bot.execute_intelligence, bot.intelligence_target
        completed = 0

        def execute(*args, **kwargs):
            nonlocal completed
            frame, issued = original_execute(*args, **kwargs)
            completed += bool(issued)
            return frame, issued

        def target(frame, excluded=()):
            if args.intelligence_limit and completed >= args.intelligence_limit:
                return None
            return original_target(frame, excluded)

        bot.execute_intelligence, bot.intelligence_target = execute, target
        try:
            frame = bot.run_intelligence(args.device, help_icon, nav, offline, reconnect, timers=timers)
            print(f"Ordinary intelligence missions executed in this verification pass: {completed}", flush=True)
            return frame
        finally:
            bot.execute_intelligence, bot.intelligence_target = original_execute, original_target

    def verify_bounty():
        frame = bot.enter_wilderness(args.device, capture(args.device), help_icon, nav, "悬赏验证")
        if bot.intelligence_queue_full(frame):
            print("Bounty unavailable: march queues are full", flush=True)
            return frame
        frame = bot.open_intelligence(args.device, frame)
        claim = bot.intelligence_text_target(frame, ("一键领取",), (280, 2000, 800, 2190))
        if claim is not None:
            frame = bot.claim_intelligence_rewards(args.device, claim)
            print("Claimed completed ordinary mission rewards before inspecting the bounty pin", flush=True)
        target = bot.bounty_target(frame)
        if target is None:
            print("Bounty unavailable: no exposed bounty pin on the current intelligence map", flush=True)
            bot.task_tap(args.device, 60, 190)
            return capture(args.device)
        state = {}
        frame, completed = bot.execute_bounty(args.device, frame, target, state)
        print(f"Bounty verification: completed={completed}, outcome={state}", flush=True)
        return frame

    def verify_treasure():
        # Sharing sends an alliance message; a general verification pass only
        # exercises collection and dispatch, without publishing to other players.
        original_share = bot.treasure_share_target
        bot.treasure_share_target = lambda image: None
        try:
            return bot.run_treasure(args.device, help_icon, nav, drawer)
        finally:
            bot.treasure_share_target = original_share

    def verify_help_clicker():
        bot.alliance_help_clicker.enter(bot, args.device, offline, reconnect)
        before = sample(bot.color_screenshot(args.device), force=True)
        if not bot.alliance_help_clicker.help_page(bot, before):
            raise RuntimeError("Alliance help title was not confirmed")
        bot.task_tap(args.device, *bot.alliance_help_clicker.HELP_ALL)
        after = capture(args.device)
        if not bot.alliance_help_clicker.help_page(bot, after):
            raise RuntimeError("Help-all tap left the confirmed alliance help page")
        print("Alliance help page and one help-all tap verified; no requests may be pending", flush=True)
        return after

    tasks = {
        "help_clicker": verify_help_clicker,
        "donate": lambda: bot.run_city_tasks(args.device, help_icon, nav, templates, recruit=False),
        **{kind: lambda kind=kind: bot.run_city_tasks(args.device, help_icon, nav, templates,
                    donate=False, recruit_kind=kind, timers=timers) for kind in bot.RECRUIT_TYPES},
        "stamina": lambda: bot.run_stamina(args.device, help_icon, nav, drawer, timers),
        "treasure": verify_treasure,
        "explore": lambda: bot.run_explore(args.device, help_icon, nav,
                    {n: asset("explore-" + n) for n in bot.EXPLORE_BOXES}, timers),
        "train": lambda: bot.run_training(args.device, help_icon, nav, drawer,
                    {unit: asset(f"training-{unit}-title") for unit in bot.UNIT_TYPES},
                    timers=timers, upgrade=True, units=set(args.train_units)),
        **{kind: lambda kind=kind: bot.run_pet_skill(args.device, help_icon, nav, drawer, kind, timers)
           for kind in bot.PET_SKILLS},
        "task_rewards": lambda: bot.task_rewards.run(bot, args.device, help_icon, nav),
        "heal": lambda: bot.run_healing(args.device, help_icon, nav),
        "intelligence": verify_intelligence,
        "bounty": verify_bounty,
        "hunt": lambda: bot.run_hunt(args.device, help_icon, nav, timers=timers),
        "gather": lambda: bot.run_gather(args.device, help_icon, nav, offline, reconnect,
                    levels={resource: args.gather_level for resource in bot.GATHER_RESOURCES}),
        "bear": lambda: bot.run_bear(args.device, help_icon, nav, offline, reconnect,
                    modes=("drive", "follow", "follow_no_heroes"), forever=False, seconds=30),
        **{f"bear_{mode}": lambda mode=mode: bot.run_bear(args.device, help_icon, nav, offline, reconnect,
                    modes=(mode,), forever=False, seconds=45)
           for mode in ("drive", "follow", "follow_no_heroes")},
        "monitor": lambda: bot.run(args.device, 30, 2, False, 10, help_icon, nav, offline, reconnect),
        **{kind: lambda kind=kind: bot.run_rewards(args.device, help_icon, nav, drawer,
                warehouse, tree, island, warehouse=kind == "warehouse", tree=kind == "tree",
                dawn=kind == "dawn", tree_threshold=1, timers=timers)
           for kind in ("warehouse", "tree", "dawn")},
    }
    if any(name not in tasks for name in args.tasks):
        parser.error("Unknown task")
    if args.device not in nox_endpoints():
        parser.error("Select an installed Nox instance; other devices cannot supply matching samples")
    with DeviceLease(args.device, avd_key(args.device)):
        if not bot.game_foreground(args.device):
            raise RuntimeError("The target game must be in the foreground before collecting Nox samples")
        model = bot.adb(args.device, "shell", "getprop", "ro.product.model").decode().strip()
        bot.task_screen = capture
        sample(bot.color_screenshot(args.device), force=True)
        failures = []
        for name in args.tasks:
            state["task"] = name
            try:
                bot.recover_task_city(args.device, help_icon, nav, offline, reconnect)
                print(f"START {name}", flush=True)
                result = tasks[name]()
                if result is not None:
                    sample(result, force=True)
                print(f"COMPLETED {name}; inspect task output for executed, cooldown or unavailable status; timers={timers}", flush=True)
            except Exception as exc:
                failures.append(name)
                print(f"FAILED {name}: {exc}", flush=True)
                sample(bot.color_screenshot(args.device), force=True)
        state["task"] = "return-city"
        bot.recover_task_city(args.device, help_icon, nav, offline, reconnect)
        print(f"Captured {len(samples)}/{len(references)} templates; failures={failures}", flush=True)
        print("PENDING " + ", ".join(sorted(references.keys() - samples.keys())), flush=True)
        if failures:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
