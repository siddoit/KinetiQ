"""KINETIQ Hardware Procurement & PCB Specification PDF Generator.

Dependency: pip install reportlab
Output: docs/KINETIQ_Hardware_Procurement_Spec.pdf
"""

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
import os

DEFAULT_OUTPUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docs",
    "KINETIQ_Hardware_Procurement_Spec.pdf",
)


def build_pdf(filename=DEFAULT_OUTPUT):
    out_dir = os.path.dirname(os.path.abspath(filename))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()

    # Custom Typography
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontSize=20,
        leading=24,
        textColor=colors.HexColor('#0F172A'),
        spaceAfter=4
    )
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#475569'),
        spaceAfter=14
    )
    h1_style = ParagraphStyle(
        'Heading1_Custom',
        parent=styles['Heading2'],
        fontSize=13,
        leading=17,
        textColor=colors.HexColor('#1E293B'),
        spaceBefore=12,
        spaceAfter=6,
        keepWithNext=True
    )
    body_style = ParagraphStyle(
        'Body_Custom',
        parent=styles['BodyText'],
        fontSize=9,
        leading=13,
        textColor=colors.HexColor('#334155'),
        spaceAfter=4
    )
    table_header = ParagraphStyle(
        'TableHeader',
        parent=styles['Normal'],
        fontSize=8,
        leading=10,
        fontName='Helvetica-Bold',
        textColor=colors.white
    )
    table_cell = ParagraphStyle(
        'TableCell',
        parent=styles['Normal'],
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor('#1E293B')
    )
    link_cell = ParagraphStyle(
        'LinkCell',
        parent=table_cell,
        textColor=colors.HexColor('#0284C7'),
        fontName='Helvetica-Bold'
    )

    story = []

    # Title Banner
    story.append(Paragraph("KINETIQ — Hardware Procurement & PCB Specification", title_style))
    story.append(Paragraph("<b>Version:</b> 1.0 (Frozen Scope) &nbsp;|&nbsp; <b>Target:</b> ESP32-S3 Wearable &nbsp;|&nbsp; <b>RTOS:</b> Zephyr 4.2+", subtitle_style))
    story.append(Spacer(1, 4))

    # Section 1: BOM
    story.append(Paragraph("1. Bill of Materials & Procurement Directory", h1_style))

    bom_data = [
        [
            Paragraph("Item", table_header),
            Paragraph("Locked MPN / Spec", table_header),
            Paragraph("Qty", table_header),
            Paragraph("Source", table_header),
            Paragraph("Footprint / Notes", table_header)
        ],
        [
            Paragraph("MCU DevKit", table_cell),
            Paragraph("ESP32-S3-DevKitC-1-N16R8", table_cell),
            Paragraph("2", table_cell),
            Paragraph('<a href="https://robu.in/product/esp32-s3-devkit-esp32-s3-wroom-1-n16r8/">Robu.in</a>', link_cell),
            Paragraph("DevKit Header Breakout", table_cell)
        ],
        [
            Paragraph("MCU Module", table_cell),
            Paragraph("ESP32-S3-WROOM-1-N16R8", table_cell),
            Paragraph("5", table_cell),
            Paragraph('<a href="https://evelta.com/esp32-s3-wroom-1-n16r8-wi-fi-ble-module-transceiver-16mb-flash-pcb-antenna/">Evelta</a>', link_cell),
            Paragraph("Castellated SMD Module", table_cell)
        ],
        [
            Paragraph("IMU (Proto)", table_cell),
            Paragraph("BMI270 Breakout", table_cell),
            Paragraph("3", table_cell),
            Paragraph('<a href="https://robocraze.com/products/7semi-bmi270-nano-6-axis-imu-qwiic-breakout-module">Robocraze</a>', link_cell),
            Paragraph("Breadboard Breakout", table_cell)
        ],
        [
            Paragraph("IMU (PCB IC)", table_cell),
            Paragraph("Bosch BMI270 IC", table_cell),
            Paragraph("5", table_cell),
            Paragraph('<a href="https://robu.in/product/bosch-bmi270motion-sensoric22-g-4-g-8-g-16-g/">Robu.in</a>', link_cell),
            Paragraph("LGA-14 (2.5x3 mm)", table_cell)
        ],
        [
            Paragraph("PPG Sensor", table_cell),
            Paragraph("MAX30102 Module", table_cell),
            Paragraph("3", table_cell),
            Paragraph('<a href="https://robu.in/product/dfrobot-fermion-max30102-heart-rate-and-oximeter-sensor-breakout/">Robu.in</a>', link_cell),
            Paragraph("Module Breakout", table_cell)
        ],
        [
            Paragraph("Display", table_cell),
            Paragraph("GC9A01 1.28\" IPS Non-Touch", table_cell),
            Paragraph("3", table_cell),
            Paragraph('<a href="https://probots.co.in/round-ips-color-display-module-gc9a01-1-28-inch.html">Probots</a>', link_cell),
            Paragraph("7-Pin SPI Header", table_cell)
        ],
        [
            Paragraph("Mic", table_cell),
            Paragraph("INMP441 I2S MEMS", table_cell),
            Paragraph("4", table_cell),
            Paragraph('<a href="https://robocraze.com/products/inmp441-mems-high-precision-omnidirectional-microphone-module-i2s">Robocraze</a>', link_cell),
            Paragraph("Breakout / Bottom port", table_cell)
        ],
        [
            Paragraph("Amp", table_cell),
            Paragraph("MAX98357A I2S Class-D", table_cell),
            Paragraph("4", table_cell),
            Paragraph('<a href="https://robu.in/product/bgamax98357a-i2s-amplifier-breakout-module/">Robu.in</a>', link_cell),
            Paragraph("Breakout Header", table_cell)
        ],
        [
            Paragraph("Speaker", table_cell),
            Paragraph("8Ω 0.5W Micro 20 mm", table_cell),
            Paragraph("4", table_cell),
            Paragraph('<a href="https://robu.in/product/1-month-warranty-462/">Robu.in</a>', link_cell),
            Paragraph("Wire Leads", table_cell)
        ],
        [
            Paragraph("Buttons", table_cell),
            Paragraph("6x6 mm Tactile SMD", table_cell),
            Paragraph("20", table_cell),
            Paragraph('<a href="https://robu.in/product/tact-switch-tk-066-4-pins-6x6-smd/">Robu.in</a>', link_cell),
            Paragraph("4-Pin SMD", table_cell)
        ],
        [
            Paragraph("Battery", table_cell),
            Paragraph("3.7V 600mAh LiPo (WLY602540)", table_cell),
            Paragraph("3", table_cell),
            Paragraph('<a href="https://robu.in/product/wly602540-600mah-3-7v-single-cell-rechargeable-lipo-battery/">Robu.in</a>', link_cell),
            Paragraph("JST-PH 2.0 / Leads", table_cell)
        ],
        [
            Paragraph("Charger IC", table_cell),
            Paragraph("TP4056 Linear Charger", table_cell),
            Paragraph("5", table_cell),
            Paragraph('<a href="https://robu.in/product/1-month-warranty-1253/">Robu.in</a>', link_cell),
            Paragraph("SOP-8 Package", table_cell)
        ],
        [
            Paragraph("LDO Regulator", table_cell),
            Paragraph("ME6211C33M5G-N 3.3V 500mA", table_cell),
            Paragraph("5", table_cell),
            Paragraph('<a href="https://robu.in/product/me6211c33m5g-n-microne-500ma-fixed-3-3v-positive-electrode-6v-sot-23-5-voltage-regulators-linear-low-drop-out-ldo-regulators-rohs/">Robu.in</a>', link_cell),
            Paragraph("SOT-23-5 Package", table_cell)
        ],
        [
            Paragraph("USB-C Jack", table_cell),
            Paragraph("Type-C 16-Pin Receptacle", table_cell),
            Paragraph("10", table_cell),
            Paragraph('<a href="https://robu.in/product/type-c-31-m-16-hroparts-1-6p-female-type-c-smd-usb/">Robu.in</a>', link_cell),
            Paragraph("16P SMD + Tabs", table_cell)
        ],
    ]

    t = Table(bom_data, colWidths=[80, 150, 30, 80, 200])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0F172A')),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8FAFC')]),
    ]))
    story.append(t)
    story.append(Spacer(1, 8))

    # Section 2: Architecture Rationale
    story.append(Paragraph("2. Component Selection Rationale & Subsystem Integration", h1_style))

    components_info = [
        ("ESP32-S3-WROOM-1-N16R8 (Brain):",
         "Selected for dual-core Xtensa performance, 8MB PSRAM, and native BLE/USB support. Using the integrated-antenna module avoids the severe RF tuning and impedance pitfalls of bare-chip layout on a 2-layer PCB. Mounted at the board edge running Zephyr RTOS tasks."),
        ("ESP32-S3-DevKitC-1 (Bring-Up Target):",
         "Matches the MCU pinout for immediate firmware bring-up while the PCB is fabricated. Serves as a drop-in contingency inside the enclosure if PCB production slips."),
        ("Bosch BMI270 (IMU):",
         "Has in-tree Zephyr driver support ('bosch,bmi270') and onboard step/motion processing. Runs on I2C0 at 100 Hz to generate continuous motion intensity and activity classifications."),
        ("MAX30102 (PPG Sensor):",
         "Integrated optical red/IR sensor supported via Zephyr 'maxim,max30102' compatibility. Faces the wrist on the bottom shell; provides resting HR and quality flags."),
        ("GC9A01 1.28\" IPS Display:",
         "Round form factor matches the smartwatch chassis. Controlled over SPI2 via direct-draw framebuffer routines. Eliminating LVGL avoids unnecessary memory overhead and keeps the 5-week timeline on track."),
        ("INMP441 Mic & MAX98357A Amp (Audio):",
         "Digital I2S audio eliminates external codecs and analog noise. INMP441 streams 16 kHz voice over I2S0-RX; MAX98357A drives the 20 mm speaker over I2S-TX without an MCLK line."),
        ("TP4056 & ME6211 Power System:",
         "The ME6211 provides 500 mA output with low ripple for RF and sensors. The TP4056 delivers safe CC/CV LiPo charging via USB-C.")
    ]

    for title, desc in components_info:
        story.append(Paragraph(f"<b>{title}</b> {desc}", body_style))

    story.append(Spacer(1, 8))

    # Section 3: PCB Rules
    story.append(Paragraph("3. PCB Layout & Electrical Design Rules", h1_style))

    rules = [
        "<b>1. ESP32-S3 Antenna Keepout:</b> Place the WROOM-1 module at the board edge. Maintain a 15 mm keepout boundary with zero copper, ground fills, traces, or vias on any layer under or adjacent to the antenna area.",
        "<b>2. USB-C CC Pull-Down Resistors:</b> CC1 and CC2 must each have an independent 5.1 kΩ pull-down resistor to GND. Never tie CC1 and CC2 together; doing so prevents charging from USB-C PD power supplies.",
        "<b>3. Native USB D+/D- Routing:</b> Route GPIO19 (D-) and GPIO20 (D+) directly to the USB receptacle as a 90 Ω differential pair over an unbroken ground plane. Match trace lengths within 0.5 mm.",
        "<b>4. Shared I2C0 Bus:</b> Terminate SDA and SCL with dedicated 4.7 kΩ pull-up resistors to the 3.3 V rail placed close to the MCU.",
        "<b>5. Passive Sizing:</b> Use standard 0603 footprints for discrete resistors and capacitors to allow reliable hand-assembly and hot-air rework during integration sprints.",
        "<b>6. Power Decoupling:</b> Place a 10 µF bulk capacitor paired with a 0.1 µF bypass capacitor directly beside every VDD pin, sensor rail, and display connector."
    ]

    for rule in rules:
        story.append(Paragraph(rule, body_style))

    doc.build(story)
    print(f"Generated: {filename}")


if __name__ == "__main__":
    build_pdf()
