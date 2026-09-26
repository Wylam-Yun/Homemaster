# ALFWorld 原生目标 / 物体 / 容器 / 放置关系参考

> 真理源文档。所有内容来自 ALFWorld 源码核对（`alfworld/gen/constants.py`、`alfworld/env/tasks.py`、`alfworld/agents/controller/oracle.py`），不是猜测。
> 用途：编养老长程任务串（`alfworld_tasksets.yaml`）时对照此文档，确保每个子任务的 `object`/`parent` 组合合法、目标可达成。

---

## 1. 六大原生目标（成功判定）

通用规则：
- 成功 = `goal_conditions_met(state)` 返回 `s == ts`（完成子条件数 = 总子条件数）。
- 判定**只读 `state.metadata`**（THOR 当下世界状态），**不看 agent 说什么**。
- "物体在容器里" = 物体的 `objectId` 出现在该容器的 `receptacleObjectIds` 列表里。
- 动作执行用 `forceAction: True`（强制执行，几乎不模拟失败）；任务成功靠事后独立读世界状态。两步分开。
- 导航 `go to X` = 瞬移到 X 前（不模拟走路径/避障）。

| # | goal_type | 成功条件（查什么） | 编任务要指定 | 隐含/固定 | ts |
|---|---|---|---|---|---|
| 1 | `pick_and_place_simple` | 任意 `object` 进任意 `parent` 的 `receptacleObjectIds` | `object`, `parent` | — | 1 |
| 2 | `pick_two_obj_and_place` | ≥2 个 `object` 进同一 `parent`（`min(...,2)` 截断） | `object`, `parent` | — | 2 |
| 3 | `look_at_obj_in_light` | `object` 在手里（`inventoryObjects[0]`）+ `toggle` 灯 `isToggled & visible` | `object`, `toggle` | — | 2 |
| 4 | `pick_heat_then_place_in_recep` | `object` 进 `parent` + 已加热（`env.heated_objects`）+ 同一物体 | `object`, `parent` | 加热器固定 = Microwave | 3 |
| 5 | `pick_cool_then_place_in_recep` | `object` 进 `parent` + 已冷却（`env.cooled_objects`）+ 同一物体 | `object`, `parent` | 冷却器固定 = Fridge | 3 |
| 6 | `pick_clean_then_place_in_recep` | `object` 进 `parent` + 已清洗（`env.cleaned_objects`）+ 同一物体 | `object`, `parent` | 清洗器 = SinkBasin 或 BathtubBasin | 3 |
| 7 | `pick_and_place_with_movable_recep` | `object` 进 `mrecep` + `mrecep` 进 `parent` + 同一摞 | `object`, `mrecep`, `parent` | ⚠️ 原生 eval 跳过 movable，**建议不用** | 3 |

> 说明：ALFWorld 官方 6 类指 #1–#6（task_types 1–6）。#7（movable）和所有带 `Sliced` 的变体在 batch env 的 `alfred_thor_env.py` 里被 `if 'movable' in root or 'Sliced' in root: continue` 跳过。**编任务串时避开 `mrecep` 和 `Sliced`**，与原生一致，避免踩坑。

### 6 类目标的加热/冷却/清洗器约束

- `pick_heat_then_place`：物体必须能放进 **Microwave**（加热器写死）。`parent`（最终容器）也必须是该物体能放的。
- `pick_cool_then_place`：物体必须能放进 **Fridge**（冷却器写死）。
- `pick_clean_then_place`：物体必须能放进 **SinkBasin** 或 **BathtubBasin**（清洗器，oracle 根据 tar 选）。

---

## 2. 容器清单（37 种 RECEPTACLES）

按家庭区域分组。标"专用"的只收一种物体，标"通用"的收多种。

### 厨房
| 容器 | 类型 | 备注 |
|---|---|---|
| CounterTop | 通用 | 厨房台面，能放 58 种物体，最灵活 |
| Cabinet | 通用 | 柜子，能放 29 种 |
| Drawer | 通用 | 抽屉，能放 28 种 |
| Shelf | 通用 | 架子，能放 36 种 |
| Fridge | 通用 | 冰箱（=冷却器），能放 19 种 |
| Microwave | 通用 | 微波炉（=加热器），能放 14 种 |
| SinkBasin | 通用 | 水槽（=清洗器），能放 27 种 |
| StoveBurner | 专用 | 灶台，只收 Kettle/Pan/Pot |
| CoffeeMachine | 专用 | 咖啡机，只收 Mug |
| Toaster | 专用 | 烤面包机，只收 BreadSliced |

### 餐厅 / 客厅
| 容器 | 类型 | 备注 |
|---|---|---|
| TableTop | 通用 | 内部统一名；DiningTable/CoffeeTable/SideTable 都映射到 TableTop（见源码 `VAL_RECEPTACLE_OBJECTS['DiningTable']=...TableTop` 并删 TableTop） |
| DiningTable / CoffeeTable / SideTable | 通用 | 编任务时用这三个名字之一，判定时等价于 TableTop |
| Desk | 通用 | 书桌，能放 31 种 |
| Dresser | 通用 | 梳妆台，能放 30 种 |
| Sofa | 通用 | 沙发，能放 11 种（Book/CellPhone/Pillow/RemoteControl 等） |
| ArmChair | 通用 | 扶手椅，能放 11 种 |
| Ottoman | 通用 | 脚凳，能放 11 种 |
| TVStand | 专用 | 电视柜，只收 TissueBox |

### 卧室
| 容器 | 类型 | 备注 |
|---|---|---|
| Bed | 通用 | 床，能放 8 种（Book/CellPhone/Laptop/Newspaper/Pillow 等） |
| Safe | 通用 | 保险箱，能放 7 种（CD/CellPhone/CreditCard/KeyChain/Statue/Vase/Watch） |
| Drawer | 通用 | 见厨房 |
| Dresser | 通用 | 见客厅 |

### 卫生间
| 容器 | 类型 | 备注 |
|---|---|---|
| BathtubBasin | 通用 | 浴缸（=清洗器之一），能放 4 种（Cloth/DishSponge/HandTowel/SoapBar） |
| SinkBasin | 通用 | 见厨房 |
| Toilet | 通用 | 马桶，能放 12 种（卫生间杂物） |
| LaundryHamper | 专用 | 洗衣篮，只收 Cloth |
| HandTowelHolder | 专用 | 毛巾架，只收 HandTowel |
| TowelHolder | 专用 | 毛巾架，只收 Towel |
| ToiletPaperHanger | 专用 | 只收 ToiletPaper / ToiletPaperRoll |

### 通用移动容器（MOVABLE_RECEPTACLES，7 种）
`Bowl` / `Box` / `Cup` / `Mug` / `Plate` / `Pan` / `Pot`

这 7 种**既是物体（能被拿），又是容器（能装东西）**。但 ⚠️ 用在 `pick_and_place_with_movable_recep` 目标里时，原生 eval 会跳过——建议**只把它们当普通物体用**（放进别的容器），不要当 mrecep。

---

## 3. 物体清单（61 种可被放置的物体）

按"养老日常"分组。每个物体后面列出"能放进哪些容器"（从 `VAL_RECEPTACLE_OBJECTS` 反向算出，**只列常用容器，完整版见 §4**）。

### 餐饮用具（移动容器，也能被放）
| 物体 | 常用可放容器 |
|---|---|
| Mug（水杯） | Cabinet, CounterTop, Shelf, Desk, Fridge, Microwave, SinkBasin, Bowl, Box, CoffeeMachine, Plate, TableTop, Dresser |
| Cup（杯子） | Cabinet, CounterTop, Shelf, Desk, Fridge, Microwave, SinkBasin, TableTop, Dresser |
| Bowl（碗） | Cabinet, CounterTop, Shelf, Desk, Fridge, Microwave, SinkBasin, TableTop, Dresser |
| Plate（盘子） | Cabinet, CounterTop, Shelf, Desk, Fridge, Microwave, SinkBasin, TableTop, Dresser |
| Pan（平底锅） | Cabinet, CounterTop, Fridge, SinkBasin, StoveBurner, TableTop |
| Pot（锅） | Cabinet, CounterTop, Fridge, Shelf, SinkBasin, StoveBurner, TableTop |
| Box（盒子） | ArmChair, Cabinet, CounterTop, Desk, Dresser, Ottoman, Shelf, Sofa, TableTop |

### 食材（可加热/冷却/清洗）
| 物体 | 常用可放容器 |
|---|---|
| Apple / Tomato / Potato / Egg | CounterTop, Fridge, Microwave, Pan, Plate, Pot, Bowl, SinkBasin, TableTop, GarbageCan |
| Bread | CounterTop, Fridge, Microwave, TableTop, GarbageCan |
| Lettuce | CounterTop, Fridge, Pan, Plate, Pot, Bowl, SinkBasin, TableTop, GarbageCan |
| Glassbottle（玻璃瓶） | Cabinet, CounterTop, Desk, Fridge, Microwave, Plate, Shelf, SinkBasin, TableTop, Dresser, Box, GarbageCan |
| WineBottle（酒瓶） | Cabinet, CounterTop, Desk, Fridge, Shelf, TableTop, Dresser, GarbageCan |
| Kettle（水壶） | Cabinet, CounterTop, Shelf, SinkBasin, StoveBurner, TableTop |

### 老人日常物品（高养老价值）
| 物体 | 常用可放容器 |
|---|---|
| RemoteControl（遥控器） | ArmChair, Bowl, Box, CounterTop, Desk, Drawer, Dresser, Ottoman, Shelf, Sofa, TableTop |
| CellPhone（手机） | ArmChair, Bed, Bowl, Box, CounterTop, Desk, Drawer, Dresser, Ottoman, Plate, Safe, Shelf, Sofa, TableTop |
| Book（书） | ArmChair, Bed, Box, Cabinet, CounterTop, Desk, Drawer, Dresser, Ottoman, Plate, Shelf, Sofa, TableTop |
| Newspaper（报纸） | ArmChair, Bed, Cabinet, CounterTop, Desk, Drawer, Dresser, GarbageCan, Ottoman, Shelf, Sofa, TableTop, Toilet |
| Pillow（枕头） | ArmChair, Bed, Ottoman, Sofa |
| Watch（手表） | Bowl, Box, CounterTop, Desk, Drawer, Dresser, Mug, Plate, Safe, Shelf, TableTop |
| KeyChain（钥匙） | ArmChair, Bowl, Box, CounterTop, Desk, Drawer, Dresser, Mug, Ottoman, Plate, Safe, Shelf, Sofa, TableTop |
| CreditCard（卡） | ArmChair, Bowl, Box, CounterTop, Desk, Drawer, Dresser, Ottoman, Plate, Safe, Shelf, Sofa, TableTop |
| CD | Bowl, Box, Cabinet, CounterTop, Desk, Drawer, Dresser, GarbageCan, Plate, Safe, Shelf, TableTop |
| Laptop（笔记本） | ArmChair, Bed, CounterTop, Desk, Dresser, Ottoman, Sofa, TableTop |
| TissueBox（纸巾盒） | Box, Cabinet, Cart, CounterTop, Desk, Drawer, Dresser, GarbageCan, Plate, Shelf, TVStand, TableTop, Toilet |
| AlarmClock（闹钟） | Box, CounterTop, Desk, Dresser, Plate, Shelf, TableTop |
| Statue（雕像/摆件） | Box, Cart, CounterTop, Desk, Dresser, Safe, Shelf, TableTop |
| Vase（花瓶） | Box, Cabinet, Cart, CounterTop, Desk, Dresser, Safe, Shelf, TableTop |

### 卫生 / 清洁用品
| 物体 | 常用可放容器 |
|---|---|
| HandTowel（小毛巾） | BathtubBasin, Cabinet, Cart, CounterTop, Drawer, GarbageCan, HandTowelHolder, Shelf, SinkBasin, TableTop, Toilet |
| Cloth（衣物） | LaundryHamper, BathtubBasin, Cabinet, Bed, Sofa, ArmChair, Shelf, SinkBasin, TableTop, Box, Bowl, Cart, CounterTop, Drawer, Dresser, GarbageCan, Ottoman, Plate, Toilet |
| Towel（毛巾） | **只能放 TowelHolder** |
| SoapBar（肥皂） | BathtubBasin, Cabinet, Cart, CounterTop, Drawer, GarbageCan, Shelf, SinkBasin, TableTop, Toilet |
| SoapBottle（洗涤液） | Cabinet, Cart, CounterTop, Desk, Drawer, GarbageCan, Shelf, TableTop, Toilet |
| SprayBottle（喷瓶） | Cabinet, Cart, CounterTop, Desk, Drawer, Dresser, GarbageCan, Shelf, TableTop, Toilet |
| DishSponge（洗碗海绵） | BathtubBasin, Bowl, Box, Cabinet, Cart, CounterTop, Drawer, GarbageCan, Pan, Plate, Pot, Shelf, SinkBasin, TableTop, Toilet |
| PaperTowel（厨房纸） | Bowl, Box, Cart, CounterTop, GarbageCan, Plate, Shelf, TableTop, Toilet |
| ToiletPaper / ToiletPaperRoll（卷纸） | Cabinet, Cart, CounterTop, Desk, Drawer, Dresser, GarbageCan, Shelf, TableTop, Toilet, ToiletPaperHanger |
| Plunger（搋子） | Cabinet, Cart |

### 餐具 / 厨具
| 物体 | 常用可放容器 |
|---|---|
| Knife（刀） | Bowl, CounterTop, Drawer, Mug, Pan, Plate, Pot, SinkBasin, TableTop |
| ButterKnife（黄油刀） | Bowl, CounterTop, Cup, Drawer, Mug, Pan, Plate, Pot, SinkBasin, TableTop |
| Fork / Spoon | Bowl, CounterTop, Cup, Drawer, Mug, Pan, Plate, Pot, SinkBasin, TableTop |
| Ladle（汤勺） | Bowl, Cabinet, CounterTop, Drawer, Pan, Plate, Pot, SinkBasin, TableTop |
| Spatula（锅铲） | Bowl, CounterTop, Drawer, Pan, Plate, Pot, SinkBasin, TableTop |
| PepperShaker / SaltShaker（调料瓶） | Cabinet, CounterTop, Drawer, Shelf, TableTop |
| Pen / Pencil | Bowl, Box, CounterTop, Desk, Drawer, Dresser, GarbageCan, Mug, Plate, Shelf, TableTop |
| Candle（蜡烛） | Bowl, Box, Cabinet, Cart, CounterTop, Desk, Drawer, Dresser, Plate, Shelf, TableTop, Toilet |
| WateringCan（浇水壶） | Cabinet, CounterTop, Desk, Drawer, Dresser, Shelf, TableTop |

### 运动 / 其他（养老关联低，备查）
| 物体 | 可放容器 |
|---|---|
| BaseballBat | Bed, CounterTop, TableTop |
| BasketBall | ArmChair, Bed, CounterTop, Desk, Dresser, Ottoman, Sofa, TableTop |
| TennisRacket | Bed, CounterTop, Desk, Dresser, TableTop |

---

## 4. 完整放置关系表（容器 → 能装哪些物体）

> 来源：`alfworld/gen/constants.py` 的 `VAL_RECEPTACLE_OBJECTS`（34 个容器条目）。
> 判定时 `DiningTable/CoffeeTable/SideTable` 等价于 `TableTop`（源码做了别名合并）。

| 容器 | 能装的物体 |
|---|---|
| ArmChair | BasketBall, Book, Box, CellPhone, Cloth, CreditCard, KeyChain, Laptop, Newspaper, Pillow, RemoteControl |
| BathtubBasin | Cloth, DishSponge, HandTowel, SoapBar |
| Bed | BaseballBat, BasketBall, Book, CellPhone, Laptop, Newspaper, Pillow, TennisRacket |
| Bowl | Apple, AppleSliced, ButterKnife, CD, Candle, CellPhone, Cloth, CreditCard, DishSponge, Egg, Fork, KeyChain, Knife, Ladle, Lettuce, LettuceSliced, Mug, PaperTowel, Pen, Pencil, Potato, PotatoSliced, RemoteControl, Spatula, Spoon, Tomato, TomatoSliced, Watch |
| Box | AlarmClock, Book, CD, Candle, CellPhone, Cloth, CreditCard, DishSponge, Glassbottle, KeyChain, Mug, PaperTowel, Pen, Pencil, RemoteControl, Statue, TissueBox, Vase, Watch |
| Cabinet | Book, Bowl, Box, CD, Candle, Cloth, Cup, DishSponge, Glassbottle, HandTowel, Kettle, Ladle, Mug, Newspaper, Pan, PepperShaker, Plate, Plunger, Pot, SaltShaker, SoapBar, SoapBottle, SprayBottle, TissueBox, ToiletPaper, ToiletPaperRoll, Vase, WateringCan, WineBottle |
| Cart | Candle, Cloth, DishSponge, HandTowel, Mug, PaperTowel, Plunger, SoapBar, SoapBottle, SprayBottle, Statue, TissueBox, ToiletPaper, ToiletPaperRoll, Vase |
| CoffeeMachine | Mug |
| CounterTop | AlarmClock, Apple, AppleSliced, BaseballBat, BasketBall, Book, Bowl, Box, Bread, BreadSliced, ButterKnife, CD, Candle, CellPhone, Cloth, CreditCard, Cup, DishSponge, Egg, Fork, Glassbottle, HandTowel, Kettle, KeyChain, Knife, Ladle, Laptop, Lettuce, LettuceSliced, Mug, Newspaper, Pan, PaperTowel, Pen, Pencil, PepperShaker, Plate, Pot, Potato, PotatoSliced, RemoteControl, SaltShaker, SoapBar, SoapBottle, Spatula, Spoon, SprayBottle, Statue, TennisRacket, TissueBox, ToiletPaper, ToiletPaperRoll, Tomato, TomatoSliced, Vase, Watch, WateringCan, WineBottle |
| Cup | ButterKnife, Fork, Spoon |
| Desk | AlarmClock, BasketBall, Book, Bowl, Box, CD, Candle, CellPhone, Cloth, CreditCard, Cup, Glassbottle, KeyChain, Laptop, Mug, Newspaper, Pen, Pencil, Plate, RemoteControl, SoapBottle, SprayBottle, Statue, TennisRacket, TissueBox, ToiletPaper, ToiletPaperRoll, Vase, Watch, WateringCan, WineBottle |
| Drawer | Book, ButterKnife, CD, Candle, CellPhone, Cloth, CreditCard, DishSponge, Fork, HandTowel, KeyChain, Knife, Ladle, Newspaper, Pen, Pencil, PepperShaker, RemoteControl, SaltShaker, SoapBar, SoapBottle, Spatula, Spoon, SprayBottle, TissueBox, ToiletPaper, ToiletPaperRoll, Watch, WateringCan |
| Dresser | AlarmClock, BasketBall, Book, Bowl, Box, CD, Candle, CellPhone, Cloth, CreditCard, Cup, Glassbottle, KeyChain, Laptop, Mug, Newspaper, Pen, Pencil, Plate, RemoteControl, SprayBottle, Statue, TennisRacket, TissueBox, ToiletPaper, ToiletPaperRoll, Vase, Watch, WateringCan, WineBottle |
| Fridge | Apple, AppleSliced, Bowl, Bread, BreadSliced, Cup, Egg, Glassbottle, Lettuce, LettuceSliced, Mug, Pan, Plate, Pot, Potato, PotatoSliced, Tomato, TomatoSliced, WineBottle |
| GarbageCan | Apple, AppleSliced, Bread, BreadSliced, CD, Cloth, DishSponge, Egg, HandTowel, Lettuce, LettuceSliced, Newspaper, PaperTowel, Pen, Pencil, Potato, PotatoSliced, SoapBar, SoapBottle, SprayBottle, TissueBox, ToiletPaper, ToiletPaperRoll, Tomato, TomatoSliced, WineBottle |
| HandTowelHolder | HandTowel |
| LaundryHamper | Cloth |
| Microwave | Apple, AppleSliced, Bowl, Bread, BreadSliced, Cup, Egg, Glassbottle, Mug, Plate, Potato, PotatoSliced, Tomato, TomatoSliced |
| Mug | ButterKnife, Fork, KeyChain, Knife, Pen, Pencil, Spoon, Watch |
| Ottoman | BasketBall, Book, Box, CellPhone, Cloth, CreditCard, KeyChain, Laptop, Newspaper, Pillow, RemoteControl |
| Pan | Apple, AppleSliced, ButterKnife, DishSponge, Egg, Fork, Knife, Ladle, Lettuce, LettuceSliced, Potato, PotatoSliced, Spatula, Spoon, Tomato, TomatoSliced |
| Plate | AlarmClock, Apple, AppleSliced, Book, ButterKnife, CD, Candle, CellPhone, Cloth, CreditCard, DishSponge, Egg, Fork, Glassbottle, KeyChain, Knife, Ladle, Lettuce, LettuceSliced, Mug, PaperTowel, Pen, Pencil, Potato, PotatoSliced, Spatula, Spoon, TissueBox, Tomato, TomatoSliced, Watch |
| Pot | Apple, AppleSliced, ButterKnife, DishSponge, Egg, Fork, Knife, Ladle, Lettuce, LettuceSliced, Potato, PotatoSliced, Spatula, Spoon, Tomato, TomatoSliced |
| Safe | CD, CellPhone, CreditCard, KeyChain, Statue, Vase, Watch |
| Shelf | AlarmClock, Book, Bowl, Box, CD, Candle, CellPhone, Cloth, CreditCard, Cup, DishSponge, Glassbottle, HandTowel, Kettle, KeyChain, Mug, Newspaper, PaperTowel, Pen, Pencil, PepperShaker, Plate, Pot, RemoteControl, SaltShaker, SoapBar, SoapBottle, SprayBottle, Statue, TissueBox, ToiletPaper, ToiletPaperRoll, Vase, Watch, WateringCan, WineBottle |
| SinkBasin | Apple, AppleSliced, Bowl, ButterKnife, Cloth, Cup, DishSponge, Egg, Fork, Glassbottle, HandTowel, Kettle, Knife, Ladle, Lettuce, LettuceSliced, Mug, Pan, Plate, Pot, Potato, PotatoSliced, SoapBar, Spatula, Spoon, Tomato, TomatoSliced |
| Sofa | BasketBall, Book, Box, CellPhone, Cloth, CreditCard, KeyChain, Laptop, Newspaper, Pillow, RemoteControl |
| StoveBurner | Kettle, Pan, Pot |
| TVStand | TissueBox |
| TableTop (= DiningTable / CoffeeTable / SideTable) | AlarmClock, Apple, AppleSliced, BaseballBat, BasketBall, Book, Bowl, Box, Bread, BreadSliced, ButterKnife, CD, Candle, CellPhone, Cloth, CreditCard, Cup, DishSponge, Egg, Fork, Glassbottle, HandTowel, Kettle, KeyChain, Knife, Ladle, Laptop, Lettuce, LettuceSliced, Mug, Newspaper, Pan, PaperTowel, Pen, Pencil, PepperShaker, Plate, Pot, Potato, PotatoSliced, RemoteControl, SaltShaker, SoapBar, SoapBottle, Spatula, Spoon, SprayBottle, Statue, TennisRacket, TissueBox, ToiletPaper, ToiletPaperRoll, Tomato, TomatoSliced, Vase, Watch, WateringCan, WineBottle |
| Toaster | BreadSliced |
| Toilet | Candle, Cloth, DishSponge, HandTowel, Newspaper, PaperTowel, SoapBar, SoapBottle, SprayBottle, TissueBox, ToiletPaper, ToiletPaperRoll |
| ToiletPaperHanger | ToiletPaper, ToiletPaperRoll |
| TowelHolder | Towel |

---

## 5. 编任务串的硬约束（避免假失败）

1. **`object` → `parent` 必须在 §4 映射表里**。例：`Mug` 放不进 `Sofa`（Sofa 不收 Mug），编 `pick_and_place_simple(Mug, Sofa)` 会永远失败。
2. **加热任务**：`object` 必须能进 Microwave（见 §4 Microwave 行），`parent` 也必须是该 `object` 能放的。
3. **冷却任务**：`object` 必须能进 Fridge（见 §4 Fridge 行），`parent` 同上。
4. **清洗任务**：`object` 必须能进 SinkBasin 或 BathtubBasin，`parent` 同上。
5. **避开 `mrecep`（movable）和 `Sliced` 变体**——原生 eval 跳过，保持一致。
6. **`look_at_obj_in_light` 的 `toggle`** 只能是 `DeskLamp` 或 `FloorLamp`（这两个不在 §4 表里，因为它们是"灯具"不是"装东西的容器"，但作为 toggle 目标合法）。

---

## 6. 一个合规的养老任务串例子（FloorPlan10）

```
1. 把水杯(Mug)从台面端到床头柜      → pick_and_place_simple(Mug, SideTable)
   ✅ Mug 能进 TableTop(=SideTable)
2. 把苹果(Apple)加热后放到餐桌      → pick_heat_then_place_in_recep(Apple, DiningTable)
   ✅ Apple 能进 Microwave(加热) + 能进 TableTop(=DiningTable)
3. 拿遥控器(RemoteControl)在落地灯下看 → look_at_obj_in_light(RemoteControl, FloorLamp)
   ✅ toggle=FloorLamp 合法
4. 把两本书(Book)收回书架            → pick_two_obj_and_place(Book, Shelf)
   ✅ Book 能进 Shelf
5. 把碗(Bowl)洗干净放回柜子          → pick_clean_then_place_in_recep(Bowl, Cabinet)
   ✅ Bowl 能进 SinkBasin(清洗) + 能进 Cabinet
6. 把衣物(Cloth)放进洗衣篮           → pick_and_place_simple(Cloth, LaundryHamper)
   ✅ Cloth 能进 LaundryHamper
```

每步都在 §4 表里查过，全部合规。判定用 ALFWorld 现成 `goal_satisfied`，不用自己写。
