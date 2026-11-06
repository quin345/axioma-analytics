import { imgChevronLeft1, imgChevronRight1 } from "../assets";

export default function ActivityCalendarPanel() {
  return (
    <div
      className="bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-col gap-[12px] items-start overflow-clip p-[14px] relative rounded-[6px] self-stretch shrink-0 w-[408px]"
      data-node-id="2007:7861"
      data-name="Activity calendar"
    >
      <div
        className="[word-break:break-word] content-stretch flex items-center justify-between leading-[1.4] not-italic overflow-clip relative shrink-0 w-full whitespace-nowrap"
        data-node-id="2007:7862"
        data-name="Panel heading"
      >
        <p
          className="font-sans font-semibold relative shrink-0 text-[#e7edf3] text-[12px]"
          data-node-id="2007:7863"
        >
          Activity by day
        </p>
        <p
          className="font-mono relative shrink-0 text-[#94a3b3] text-[9px]"
          data-node-id="2007:7864"
        >
          EUR/USD
        </p>
      </div>
      <div
        className="content-stretch flex items-start justify-between overflow-clip relative shrink-0 w-full"
        data-node-id="2007:7865"
        data-name="Calendar navigation"
      >
        <p
          className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#e7edf3] text-[11px] whitespace-nowrap"
          data-node-id="2007:7866"
        >
          October 2026
        </p>
        <div
          className="content-stretch flex gap-[12px] items-start overflow-clip relative shrink-0"
          data-node-id="2007:7867"
          data-name="Month controls"
        >
          <div
            className="relative shrink-0 size-[14px]"
            data-node-id="2007:8495"
            data-name="chevron-left"
          >
            <img
              alt=""
              className="absolute block inset-0 max-w-none size-full"
              src={imgChevronLeft1}
            />
          </div>
          <div
            className="relative shrink-0 size-[14px]"
            data-node-id="2007:8498"
            data-name="chevron-right"
          >
            <img
              alt=""
              className="absolute block inset-0 max-w-none size-full"
              src={imgChevronRight1}
            />
          </div>
        </div>
      </div>
      <div
        className="content-stretch flex gap-[4px] items-start overflow-clip relative shrink-0 w-full"
        data-node-id="2007:7870"
        data-name="Weekday headings"
      >
        <div
          className="content-stretch flex flex-[1_0_0] items-start justify-center min-w-px overflow-clip relative"
          data-node-id="2007:7871"
          data-name="Weekday"
        >
          <p
            className="[word-break:break-word] font-sans font-normal leading-[1.4] not-italic relative shrink-0 text-[#94a3b3] text-[9px] whitespace-nowrap"
            data-node-id="2007:7872"
          >
            M
          </p>
        </div>
        <div
          className="content-stretch flex flex-[1_0_0] items-start justify-center min-w-px overflow-clip relative"
          data-node-id="2007:7873"
          data-name="Weekday"
        >
          <p
            className="[word-break:break-word] font-sans font-normal leading-[1.4] not-italic relative shrink-0 text-[#94a3b3] text-[9px] whitespace-nowrap"
            data-node-id="2007:7874"
          >
            T
          </p>
        </div>
        <div
          className="content-stretch flex flex-[1_0_0] items-start justify-center min-w-px overflow-clip relative"
          data-node-id="2007:7875"
          data-name="Weekday"
        >
          <p
            className="[word-break:break-word] font-sans font-normal leading-[1.4] not-italic relative shrink-0 text-[#94a3b3] text-[9px] whitespace-nowrap"
            data-node-id="2007:7876"
          >
            W
          </p>
        </div>
        <div
          className="content-stretch flex flex-[1_0_0] items-start justify-center min-w-px overflow-clip relative"
          data-node-id="2007:7877"
          data-name="Weekday"
        >
          <p
            className="[word-break:break-word] font-sans font-normal leading-[1.4] not-italic relative shrink-0 text-[#94a3b3] text-[9px] whitespace-nowrap"
            data-node-id="2007:7878"
          >
            T
          </p>
        </div>
        <div
          className="content-stretch flex flex-[1_0_0] items-start justify-center min-w-px overflow-clip relative"
          data-node-id="2007:7879"
          data-name="Weekday"
        >
          <p
            className="[word-break:break-word] font-sans font-normal leading-[1.4] not-italic relative shrink-0 text-[#94a3b3] text-[9px] whitespace-nowrap"
            data-node-id="2007:7880"
          >
            F
          </p>
        </div>
        <div
          className="content-stretch flex flex-[1_0_0] items-start justify-center min-w-px overflow-clip relative"
          data-node-id="2007:7881"
          data-name="Weekday"
        >
          <p
            className="[word-break:break-word] font-sans font-normal leading-[1.4] not-italic relative shrink-0 text-[#94a3b3] text-[9px] whitespace-nowrap"
            data-node-id="2007:7882"
          >
            S
          </p>
        </div>
        <div
          className="content-stretch flex flex-[1_0_0] items-start justify-center min-w-px overflow-clip relative"
          data-node-id="2007:7883"
          data-name="Weekday"
        >
          <p
            className="[word-break:break-word] font-sans font-normal leading-[1.4] not-italic relative shrink-0 text-[#94a3b3] text-[9px] whitespace-nowrap"
            data-node-id="2007:7884"
          >
            S
          </p>
        </div>
      </div>
      <div
        className="content-stretch flex flex-col gap-[4px] items-start overflow-clip relative shrink-0 w-full"
        data-node-id="2007:7885"
        data-name="Calendar weeks"
      >
        <div
          className="content-stretch flex gap-[4px] items-start overflow-clip relative shrink-0 w-full"
          data-node-id="2007:7886"
          data-name="Week"
        >
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7887"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#647587] text-[10px] whitespace-nowrap"
              data-node-id="2007:7888"
            >{` `}</p>
          </div>
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7889"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#647587] text-[10px] whitespace-nowrap"
              data-node-id="2007:7890"
            >{` `}</p>
          </div>
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7891"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#647587] text-[10px] whitespace-nowrap"
              data-node-id="2007:7892"
            >{` `}</p>
          </div>
          <div
            className="bg-[#171e27] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col gap-[3px] h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7893"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#e7edf3] text-[10px] whitespace-nowrap"
              data-node-id="2007:7894"
            >
              1
            </p>
            <div
              className="bg-[#79d9ee] h-[2px] opacity-60 relative shrink-0 w-[20px]"
              data-node-id="2007:7895"
              data-name="Activity intensity"
            />
          </div>
          <div
            className="bg-[#171e27] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col gap-[3px] h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7896"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#e7edf3] text-[10px] whitespace-nowrap"
              data-node-id="2007:7897"
            >
              2
            </p>
            <div
              className="bg-[#79d9ee] h-[2px] opacity-80 relative shrink-0 w-[25px]"
              data-node-id="2007:7898"
              data-name="Activity intensity"
            />
          </div>
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7899"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#e7edf3] text-[10px] whitespace-nowrap"
              data-node-id="2007:7900"
            >
              3
            </p>
          </div>
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7901"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#e7edf3] text-[10px] whitespace-nowrap"
              data-node-id="2007:7902"
            >
              4
            </p>
          </div>
        </div>
        <div
          className="content-stretch flex gap-[4px] items-start overflow-clip relative shrink-0 w-full"
          data-node-id="2007:7903"
          data-name="Week"
        >
          <div
            className="bg-[#171e27] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col gap-[3px] h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7904"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#e7edf3] text-[10px] whitespace-nowrap"
              data-node-id="2007:7905"
            >
              5
            </p>
            <div
              className="bg-[#79d9ee] h-[2px] opacity-80 relative shrink-0 w-[25px]"
              data-node-id="2007:7906"
              data-name="Activity intensity"
            />
          </div>
          <div
            className="bg-[#171e27] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col gap-[3px] h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7907"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#e7edf3] text-[10px] whitespace-nowrap"
              data-node-id="2007:7908"
            >
              6
            </p>
            <div
              className="bg-[#79d9ee] h-[2px] opacity-40 relative shrink-0 w-[15px]"
              data-node-id="2007:7909"
              data-name="Activity intensity"
            />
          </div>
          <div
            className="bg-[#171e27] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col gap-[3px] h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7910"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#e7edf3] text-[10px] whitespace-nowrap"
              data-node-id="2007:7911"
            >
              7
            </p>
            <div
              className="bg-[#79d9ee] h-[2px] opacity-60 relative shrink-0 w-[20px]"
              data-node-id="2007:7912"
              data-name="Activity intensity"
            />
          </div>
          <div
            className="bg-[#171e27] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col gap-[3px] h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7913"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#e7edf3] text-[10px] whitespace-nowrap"
              data-node-id="2007:7914"
            >
              8
            </p>
            <div
              className="bg-[#79d9ee] h-[2px] opacity-80 relative shrink-0 w-[25px]"
              data-node-id="2007:7915"
              data-name="Activity intensity"
            />
          </div>
          <div
            className="bg-[#163b47] border border-[#79d9ee] border-solid content-stretch flex flex-[1_0_0] flex-col gap-[3px] h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7916"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#79d9ee] text-[10px] whitespace-nowrap"
              data-node-id="2007:7917"
            >
              9
            </p>
            <div
              className="bg-[#79d9ee] h-[2px] relative shrink-0 w-[15px]"
              data-node-id="2007:7918"
              data-name="Activity intensity"
            />
          </div>
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7919"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#647587] text-[10px] whitespace-nowrap"
              data-node-id="2007:7920"
            >
              10
            </p>
          </div>
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7921"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#647587] text-[10px] whitespace-nowrap"
              data-node-id="2007:7922"
            >
              11
            </p>
          </div>
        </div>
        <div
          className="content-stretch flex gap-[4px] items-start overflow-clip relative shrink-0 w-full"
          data-node-id="2007:7923"
          data-name="Week"
        >
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7924"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#647587] text-[10px] whitespace-nowrap"
              data-node-id="2007:7925"
            >
              12
            </p>
          </div>
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7926"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#647587] text-[10px] whitespace-nowrap"
              data-node-id="2007:7927"
            >
              13
            </p>
          </div>
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7928"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#647587] text-[10px] whitespace-nowrap"
              data-node-id="2007:7929"
            >
              14
            </p>
          </div>
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7930"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#647587] text-[10px] whitespace-nowrap"
              data-node-id="2007:7931"
            >
              15
            </p>
          </div>
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7932"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#647587] text-[10px] whitespace-nowrap"
              data-node-id="2007:7933"
            >
              16
            </p>
          </div>
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7934"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#647587] text-[10px] whitespace-nowrap"
              data-node-id="2007:7935"
            >
              17
            </p>
          </div>
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7936"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#647587] text-[10px] whitespace-nowrap"
              data-node-id="2007:7937"
            >
              18
            </p>
          </div>
        </div>
        <div
          className="content-stretch flex gap-[4px] items-start overflow-clip relative shrink-0 w-full"
          data-node-id="2007:7938"
          data-name="Week"
        >
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7939"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#647587] text-[10px] whitespace-nowrap"
              data-node-id="2007:7940"
            >
              19
            </p>
          </div>
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7941"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#647587] text-[10px] whitespace-nowrap"
              data-node-id="2007:7942"
            >
              20
            </p>
          </div>
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7943"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#647587] text-[10px] whitespace-nowrap"
              data-node-id="2007:7944"
            >
              21
            </p>
          </div>
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7945"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#647587] text-[10px] whitespace-nowrap"
              data-node-id="2007:7946"
            >
              22
            </p>
          </div>
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7947"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#647587] text-[10px] whitespace-nowrap"
              data-node-id="2007:7948"
            >
              23
            </p>
          </div>
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7949"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#647587] text-[10px] whitespace-nowrap"
              data-node-id="2007:7950"
            >
              24
            </p>
          </div>
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7951"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#647587] text-[10px] whitespace-nowrap"
              data-node-id="2007:7952"
            >
              25
            </p>
          </div>
        </div>
        <div
          className="content-stretch flex gap-[4px] items-start overflow-clip relative shrink-0 w-full"
          data-node-id="2007:7953"
          data-name="Week"
        >
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7954"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#647587] text-[10px] whitespace-nowrap"
              data-node-id="2007:7955"
            >
              26
            </p>
          </div>
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7956"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#647587] text-[10px] whitespace-nowrap"
              data-node-id="2007:7957"
            >
              27
            </p>
          </div>
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7958"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#647587] text-[10px] whitespace-nowrap"
              data-node-id="2007:7959"
            >
              28
            </p>
          </div>
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7960"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#647587] text-[10px] whitespace-nowrap"
              data-node-id="2007:7961"
            >
              29
            </p>
          </div>
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7962"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#647587] text-[10px] whitespace-nowrap"
              data-node-id="2007:7963"
            >
              30
            </p>
          </div>
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7964"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#647587] text-[10px] whitespace-nowrap"
              data-node-id="2007:7965"
            >
              31
            </p>
          </div>
          <div
            className="bg-[#11161d] border border-[#11161d] border-solid content-stretch flex flex-[1_0_0] flex-col h-[34px] items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            data-node-id="2007:7966"
            data-name="Day activity"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#647587] text-[10px] whitespace-nowrap"
              data-node-id="2007:7967"
            >{` `}</p>
          </div>
        </div>
      </div>
      <div
        className="[word-break:break-word] content-stretch flex items-start justify-between leading-[1.4] not-italic overflow-clip relative shrink-0 w-full whitespace-nowrap"
        data-node-id="2007:7968"
        data-name="Selected day"
      >
        <p
          className="font-mono relative shrink-0 text-[#79d9ee] text-[10px]"
          data-node-id="2007:7969"
        >
          09 OCT · 184,320 ticks
        </p>
        <p
          className="font-sans font-normal relative shrink-0 text-[#94a3b3] text-[9px]"
          data-node-id="2007:7970"
        >
          Selected 4h
        </p>
      </div>
      <p
        className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#94a3b3] text-[9px] whitespace-nowrap"
        data-node-id="2007:7971"
      >
        Low ▂ ▃ ▅ High · weekdays / UTC
      </p>
    </div>
  );
}
