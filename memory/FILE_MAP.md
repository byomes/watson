# Watson File Map
*Generated: 2026-07-01*
*Excludes: logs/, data/chroma/, kb/documents/, kb/transcripts/, .git/, node_modules/, venv/, __pycache__/, .next/, outputs/, .claude/*

## ~/watson/

```
~/watson/
.env
.env.example
.env.local
.gitignore
.vercel/
  README.txt
  project.json
CLAUDE.md
README.md
bot/
  __init__.py
  bot.py
briefing/
  __init__.py
  app.py
  builder.py
  publisher.py
  templates/
    .gitkeep
    briefing.html
    briefing_static.html
    dashboard.html
    library.html
    reading-list.html
    research_library.html
    sources.html
    thought_library.html
config/
  credentials.json
  settings.py
  sources.yaml
  token.json
core/
  __init__.py
  database.py
  fetcher.py
  pipeline.py
  scorer.py
  summarizer.py
cron/
  .gitkeep
  run_pipeline.sh
cron_additions.txt
data/
  .gitkeep
  congregation.db
  donors.db
  exports/
    qr_1780863713.png
    qr_1780883260.png
    qr_1781037801.png
    qr_1781038684.png
    qr_1781039900.png
    qr_1781040379.png
    qr_1781042385.png
  qr/
    qr_20260608_215150.png
    qr_20260608_220849.png
    qr_20260608_220942.png
    qr_20260608_221107.png
    qr_20260608_221900.png
    qr_20260608_222321.png
    qr_20260608_224205.png
    qr_20260608_225414.png
    qr_20260608_225539.png
    qr_20260608_225619.png
    qr_20260608_230413.png
    qr_20260609_164058.png
    qr_20260611_113515.png
    qr_20260611_114332.png
    qr_20260611_114434.png
    qr_20260611_114601.png
    qr_20260611_114652.png
    qr_20260611_114932.png
    qr_20260611_115908.png
  riddle_history.json
  skill_audit.json
  watson.db
deploy/
  .gitkeep
  connect_cards_cron.txt
  index.html
  people-server.service
  start_people_server.sh
  watson-dashboard.service
dev/
  csv-json-final/
    main.py
    spec.md
  weather/
    main.py
docs/
  .gitkeep
  briefing.html
import_connect_cards.py
import_contacts.py
jobs/
  __init__.py
  acquired/
    chump.py
    send.py
  arc/
    __init__.py
    api.py
    auth.py
    commitment_validator.py
    send_invite_email.py
    send_signup_confirmation.py
    templates/
      arc_invite_email.html
  ask.py
  backup.py
  batch.py
  bible.py
  build_kb.py
  cleanup.py
  cleanup_library.ps1
  code_agent/
    __init__.py
    agent.py
    confirm.py
    prompts/
      build.md
  congregation/
    __init__.py
    batch_intake.py
    init_db.py
    member_match.py
    migrate_reparse.py
  connect_cards/
    __init__.py
    attendance_intake.py
    backfill.py
    campus_classifier.py
    conflict_report.py
    correction_handler.py
    data_audit.py
    email_reports.py
    intake.py
    migrate_prayer_leadership.py
    missed_report.py
    pastoral_reports.py
    report_menu.py
    reports.py
    shepherding_report.py
    state_of_church.py
    utils.py
  contacts/
    __init__.py
    vcf_importer.py
  dadjoke/
    __init__.py
    joke.py
  dashboard/
    app.py
    app.py.bak
    migrate_admin.py
    migrate_sessions.py
    static/
      favicon-w.svg
      favicon.svg
      style.css
      team.js
      watson.js
    templates/
      admin.html
      admin_login.html
      index.html
      team.html
  data/
    __init__.py
    chart_generator.py
    data_analyzer.py
    table_extractor.py
  design/
    __init__.py
    image_tools.py
    screenshot.py
    svg_generator.py
  dev/
    __init__.py
    auto_fixer.py
    build_memory_store.py
    build_pipeline.py
    claude_api_final_review.py
    claude_debug.py
    code_agent.py
    code_analyzer.py
    code_editor.py
    code_quality.py
    command_executor.py
    dependency_manager.py
    dependency_scanner.py
    error_analyzer.py
    file_map.py
    git_tools.py
    github_tools.py
    hello_dashboard.py
    performance_profiler.py
    secrets_audit.py
    skill_tester.py
    skill_validator.py
    smoke_test_triggers.py
    system_monitor.py
    test_runner.py
    update_arch.py
  dev_loop/
    __init__.py
    cleanup.py
    deliver.py
    loop.py
    trigger.py
  documents/
    __init__.py
    excel.py
    pdf.py
    powerpoint.py
    word.py
  email_intake.py
  email_job/
    __init__.py
    draft_email.py
    email_queue.py
    gmail.py
  email_reply/
    __init__.py
    drafter.py
    handler.py
    reader.py
  email_send/
    __init__.py
    send.py
  facebook/
    __init__.py
    facebook_post.py
    scheduler.py
    templates.py
  gcal/
    __init__.py
    availability.py
    create_event.py
    gcal_service.py
    notify.py
    pending.py
    pre_meeting_brief.py
    reasoner.py
    reauth.py
    token_health.py
  generate.py
  givebutter/
    __init__.py
    notify.py
    sync.py
    templates.py
  ingest_drafts.py.retired
  intent/
    __init__.py
    classifier.py
  kb/
    __init__.py
    archive_transcripts.py
  marketing/
    __init__.py
    content_calendar.py
    seo_tools.py
    social_poster.py
  media/
    __init__.py
    audio_tools.py
    youtube_downloader.py
  memory/
    __init__.py
    new_project.py
    prompt_builder.py
    propose.py
    reflect.py
    sync.py
    wrap_up.py
  memory_manager.py
  misc/
    __init__.py
    both_read_pdf.py
    here_link_book.py
    im_trying_file.py
    riddle.py
    tells_many_days.py
    update_your_own.py
  monitoring/
    __init__.py
    log_watch.py
    weather_every_morning.py
  note.py
  pastoral_notes/
    __init__.py
    db.py
    handler.py
    prompt.py
    reminder.py
  people/
    __init__.py
    api.py
    google_contacts.py
    lookup.py
    migrate.py
    registry.py
    server.py
  qr/
    __init__.py
    qr_generate.py
  reading_list.py
  reminders/
    __init__.py
    check_reminders.py
    check_timed.py
    daily_summary.py
  research/
    __init__.py
    academic_search.py
    article_reader.py
    feed_reader.py
    isbn_lookup.py
    language_detector.py
    news_search.py
    semantic_search.py
    summarizer.py
    web_search.py
  scheduler.py
  security/
    __init__.py
    encryptor.py
  skillbuilder/
    __init__.py
    acquire.py
    audit.py
    build.py
    research.py
    router.py
  skills/
    __init__.py
    book_appointment.py
    cdb_query.py
    contacts_lookup.py
    kb_export.py
    kb_search.py
    logins.py
    pastoral_search.py
    polish.py
    wdb_query.py
  sms/
    __init__.py
    sms_send.py
  social/
    __init__.py
    image_search.py
  tasks/
    __init__.py
    add_task.py
  team/
    __init__.py
    api.py
    contact_sync.py
    email_job.py
    extractor.py
    inbound.py
    migrate.py
    migrate_tasks.py
    note_task_scan.py
    pre_meeting.py
    reminders.py
    weekly_completed_report.py
  telegram/
    __init__.py
    pending.py
    resend_last.py
  time_check.py
  transcribe.py
  utilities/
    __init__.py
    calendar_importer.py
    date_helper.py
    template_engine.py
    text_processor.py
  watcher.py
  web/
    __init__.py
    page_generator.py
    site_deployer.py
  writing/
    __init__.py
    citation_manager.py
    document_converter.py
    epub_generator.py
    grammar_checker.py
    manuscript_tracker.py
    readability.py
    spell_checker.py
    style_checker.py
    wordcloud_generator.py
  writing_room/
    __init__.py
    api.py
    monitor.py
    onboard.py
    remind.py
    reset.py
    send_arc_welcome_email.py
    templates/
      arc_welcome_email.html
kb/
  .collection_id_cache.json
  bible-studies/
    Acts 01-01-14.txt
    Acts 01-1-26.txt
    Acts 01-15-26.txt
    Acts 01.txt
    Acts 02-01-13.txt
    Acts 02-1-21.txt
    Acts 02-14-24.txt
    Acts 02-22-47.txt
    Acts 02-25-36.txt
    Acts 02-36-47.txt
    Acts 03 alt copy.txt
    Acts 03.txt
    Acts 04.txt
    Acts 1-2.txt
    Acts 10-1-23.txt
    Acts 10-24-48.txt
    Acts 11-1-30.txt
    Acts 12-1-19.txt
    Acts 13-1-26.txt
    Acts 13-26-52.txt
    Acts 14-1-28.txt
    Acts 15-1-35.txt
    Acts 15-36-16-15.txt
    Acts 16-16-40.txt
    Acts 17-1-15.txt
    Acts 17-16-34.txt
    Acts 18-1-17.txt
    Acts 18-18-28.txt
    Acts 19-1-22.txt
    Acts 19-23-41.txt
    Acts 20-1-12.txt
    Acts 20-13-38.txt
    Acts 21-1-16.txt
    Acts 21-17-36.txt
    Acts 21-37-22-21.txt
    Acts 22-22-23-11.txt
    Acts 23-12-23-35.txt
    Acts 24-1-27.txt
    Acts 25-1-27.txt
    Acts 26-1-32.txt
    Acts 27-1-44.txt
    Acts 4-1-31.txt
    Acts 4-32-5-11.txt
    Acts 5-12-42.txt
    Acts 6-1-15.txt
    Acts 7-1-53.txt
    Acts 7-54-8-25.txt
    Acts 8-26-40.txt
    Acts 9-1-31.txt
    Acts 9-32-43.txt
    Copy of Acts 28-1-31.txt
    Genesis - An introduction.md
    Genesis 01-01-13.txt
    Genesis 01-14-02-04.txt
    Genesis 1-1-13.txt
    Genesis 1-14-2-4.txt
    Genesis 10.txt
    Genesis 11.txt
    Genesis 12.txt
    Genesis 13.txt
    Genesis 14.txt
    Genesis 15.txt
    Genesis 16.txt
    Genesis 17.txt
    Genesis 18.txt
    Genesis 19.txt
    Genesis 2-4-25.txt
    Genesis 20.txt
    Genesis 21.txt
    Genesis 22.txt
    Genesis 23.txt
    Genesis 24.txt
    Genesis 25.txt
    Genesis 26.txt
    Genesis 27.txt
    Genesis 28.txt
    Genesis 29-1-30.txt
    Genesis 3.txt
    Genesis 30.txt
    Genesis 31.txt
    Genesis 32.txt
    Genesis 33.txt
    Genesis 34.txt
    Genesis 35.txt
    Genesis 36.txt
    Genesis 37.txt
    Genesis 38.txt
    Genesis 39.txt
    Genesis 4.txt
    Genesis 40.txt
    Genesis 41.txt
    Genesis 42.txt
    Genesis 43.txt
    Genesis 44.txt
    Genesis 45.txt
    Genesis 46.txt
    Genesis 47.txt
    Genesis 48.txt
    Genesis 49.txt
    Genesis 5.txt
    Genesis 50.txt
    Genesis 6.txt
    Genesis 7.txt
    Genesis 8.txt
    Genesis 9.txt
    John  13-1-17.txt
    John 1-1-14.txt
    John 1-15-34.txt
    John 1-35-51.txt
    John 10-1-21.txt
    John 10-22-42.txt
    John 11-1-44.txt
    John 11-45-57.txt
    John 12-1-19.txt
    John 12-20-50.txt
    John 13-1-17.txt
    John 14-1-14.txt
    John 14-15-31.txt
    John 15.txt
    John 16-1-15.txt
    John 16-16-33.txt
    John 17.txt
    John 18-1-27.txt
    John 18-28-40.txt
    John 19-1-16.txt
    John 19-17-42.txt
    John 2-1-12.txt
    John 2-13-25.txt
    John 20-1-18.txt
    John 3-1-21-Sample.txt
    John 3-1-21.txt
    John 3-22-36.txt
    John 4-1-30.txt
    John 4-31-42.txt
    John 5-1-30.txt
    John 5-31-47.txt
    John 6-1-15.txt
    John 6-16-21.txt
    John 6-22-59.txt
    John 7-1-124.txt
    John 7-25-36.txt
    John 7-37-53.txt
    John 8-1-11.txt
    John 8-12-20.txt
    John 8-21-30.txt
    John 8-31-47.txt
    John 8-48-58.txt
    John 9.txt
    Joshua 01-01-18.txt
    Joshua 02-01-24.txt
    Joshua 03-01-17.txt
    Joshua 04-01-23.txt
    Joshua 05-01-15.txt
    Joshua 06-01-27.txt
    Joshua 07-01-26.txt
    Joshua 08-01-35.txt
    Joshua 09-01-27.txt
    Joshua 10-01-15.txt
    Joshua 10-16-43.txt
    Joshua 11-1-23.txt
    Joshua 12-1-24.txt
    Joshua 13-1-19-51.txt
    Joshua 20-1-21-45.txt
    Levitical and Cities of Refuge.txt
    Luke 01-1-25.txt
    Luke 01-26-38.txt
    Luke 01-39-56.txt
    Luke 01-57-80.txt
    Luke 02-1-21.txt
    Luke 02-22-40.txt
    Luke 02-41-53.txt
    Luke 03-1-20.txt
    Luke 04-1-13.txt
    Luke 04-14-30.txt
    Luke 04-31-44.txt
    Luke 05-1-11.txt
    Luke 05-12-26.txt
    Luke 05-27-39.txt
    Luke 06-1-16.txt
    Luke 06-17-36.txt
    Luke 06-37-49.txt
    Luke 07-1-17.txt
    Luke 07-18-35.txt
    Luke 07-36-50.txt
    Luke 08-1-15.txt
    Luke 08-16-39.txt
    Luke 08-40-56.txt
    Luke 09-1-17.txt
    Luke 09-18-36.txt
    Luke 09-37-50.txt
    Luke 09-51-62.txt
    Luke 10-1-16.txt
    Luke 10-17-42.txt
    Luke 11-1-13.txt
    Luke 11-14-28.txt
    Luke 11-29-36.txt
    Luke 11-37-54.txt
    Luke 12-1-12.txt
    Luke 12-13-34.txt
    Luke 12-35-46.txt
    Luke 12-35-48.txt
    Luke 12-49-13-9.txt
    Luke 13-10-35.txt
    Luke 14-1-24.txt
    Luke 14-25-34.txt
    Luke 15-1-10.txt
    Luke 15-11-32.txt
    Luke 16-1-18.txt
    Luke 16-19-31.txt
    Luke 17-1-19.txt
    Luke 17-20-37.txt
    Luke 18-1-14.txt
    Luke 18-15-30.txt
    Luke 18-31-43.txt
    Luke 19-1-27.txt
    Luke 19-28-48.txt
    Luke 20-1-19.txt
    Luke 20-20-47.txt
    Luke 21-1-28.txt
    Luke 21-29-22-6.txt
    Luke 22-39-53.txt
    Luke 22-54-71.txt
    Luke 22-7-38.txt
    Luke 23-1-25.txt
    Luke 23-26-49.txt
    Luke 23-50-24-12.txt
    Luke 24-13-53.txt
    Matthew 9-18-26.txt
    Romans 01-01-07.txt
    Romans 01-08-17.txt
    Romans 01-18-32.txt
    Romans 02-01-16.txt
    Romans 02-17-03-08.txt
    Romans 03-09-20.txt
    Romans 03-21-31.txt
    Romans 04-01-12.txt
    Romans 04-13-25.txt
    Romans 05-01-11.txt
    Romans 05-12-21.txt
    Romans 06-15-23.txt
    Romans 07-01-13.txt
    Romans 07-14-25.txt
    Romans 08-01-11.txt
    Romans 08-12-23.txt
    Romans 08-12-25.txt
    Romans 08-26-30.txt
    Romans 08-31-39.txt
    Romans 09-01-14.txt
    Romans 09-15-33.txt
    Romans 10-1-21.txt
    Romans 11-1-21.txt
    Romans 11-22-36.txt
    Romans 12-1-2.txt
    Romans 12-3-8.txt
    Romans 12-9-21.txt
    Romans 13-01-05.txt
    Romans 13-06-14.txt
    Romans 14-1-12.txt
    Romans 14-13-23.txt
    Romans 15-1-13.txt
    Romans 15-14-33.txt
    Romans 16-1-27.txt
    The Construction of the Ark.txt
    Tribal Distribution of the Land.txt
    ~ Romans  - An Outline.md
    ~Romans - An Introduction.md
    ~Romans - An Introduction.txt
    ~romans_big_picture.txt
  books/
    GODFIDENCE.pdf
    He Is Risen KINDLE.docx
  handouts/
    Bible Study Method Banner 2m x 3m.txt
    Bible Study Method.txt
    Bible Time Periods Banner 2m x 3m.txt
    Bible Time Periods.txt
    Big Bible Story Banner 2m x 3m.txt
    Big Bible Story.txt
    Communion.txt
    FMS Speaking.txt
    Handout - Worldviews Worksheet.txt
    Maturity Cycle.txt
    Worldviews Worksheet - w Spanish.txt
    Worldviews Worksheet.txt
  sermon-notes/
    00 - Introduction to the book of Joshua.md
    00 - Introduction to the book of Joshua.txt
    According to the Angels 2024.txt
    Age of the Earth.txt
    Back to the Basics_ Giving.txt
    Back to the Basics_ Prayer.txt
    Back to the Basics_ The Gospel.txt
    Back to the Basics_ Worship.txt
    Better - Christian Community.txt
    Better - Faithful.txt
    Better - Holy.txt
    Better - Servant.txt
    Better - Worship.txt
    Big Questions.txt
    Biological Evolution.txt
    Book of James.txt
    Book of Ruth - Chapter Four.txt
    Book of Ruth - Chapter One.txt
    Book of Ruth - Chapter Three.txt
    Book of Ruth - Chapter Two.txt
    Christ in Culture 07-20-2025.txt
    Christian Worldview 2.txt
    Christian Worldview.txt
    Christmas 2025 - The Dating of Christmas.txt
    Christmas 2025 - The Expectation of Christmas.txt
    Christmas 2025 - The Lineage of Christmas.txt
    Christmas 2025 - The Witnesses of Christmas.txt
    Christmas 2025.txt
    Christmas_ Joseph the Carpenter.txt
    Christmas_ Mary.txt
    Dealing with Difficulty.txt
    Easter Sunday 2023 - Is He Risen.txt
    Easter Sunday 2024.txt
    FAM - Biblical Marriage.txt
    FAM - Childship.txt
    FAM - Family Foundation.txt
    FAM - Husbandry.txt
    FAM - Wifery.txt
    FLOOD 01 - The Pre Flood World.txt
    FLOOD 02 - Noah In His World.txt
    FLOOD 03 - The Ark.txt
    FLOOD 04 - Dinosuars.txt
    FLOOD 05 - The Flood.txt
    Faith At The Cross.txt
    Faith at the Cross Social.txt
    Family Series.txt
    Flood VBS - Construction of the Ark.txt
    Flood VBS - Dinosaurs.txt
    Flood VBS - Genesis Flood.txt
    Flood VBS - Noah and the Corrupted World.txt
    Flood VBS - PreFlood World.txt
    Flood VBS - The Ark.txt
    Flood VBS.txt
    GIFTED 2.txt
    GIFTED.txt
    GONE - Easter 2026.txt
    GROW - Disciple Maker.txt
    GROW - Follower.txt
    GROW.txt
    Gifted  - 07 - Dangers to Avoid.txt
    Gifted - 01 - Introduction to Spritual Gifts.txt
    Gifted - 02 - Why Different Gifts.txt
    Gifted - 03 - Supremecy of Love.txt
    Gifted - 04 - Speaking in Tongues.txt
    Gifted - 05 - Tongues & Prophecy.txt
    Gifted - 06 - Spiritual Gifts in Worship.txt
    Grow - Invest in Others - Social.txt
    Grow - Invest in Others.txt
    Grow - Lifestyle Obedience.txt
    Grow - Trust in Jesus.txt
    Habits for Health Fasting.txt
    Habits for Health Prayer.txt
    Habits for Health Serving.txt
    Habits for Health the Bible.txt
    Habits for Health.txt
    Habits for Health_ Stewardship.txt
    Haggai - Ch 1.txt
    Haggai-Ch2.txt
    Haggai.txt
    Hall of Heroes  David.txt
    Hall of Heroes - Catalyst.txt
    Hall of Heroes Daniel.txt
    Hall of Heroes Esther.txt
    Hall of Heroes Moses.txt
    Hall of Heroes_ Gideon.txt
    Heresy - Docetism.txt
    Heresy - Ebionism.txt
    Heresy - Gnosticism.txt
    Heresy.txt
    Holy New Year - Pastor Bill Yomes - January 01, 2023 at Catalyst.txt
    How To Live As A Christian.txt
    Human Evolution.txt
    I Am A Catalsyt 4.txt
    I Am A Catalyst 2.txt
    I Am A Catalyst 5.txt
    I Am A Catalyst 6.txt
    I Am A Catalyst Sermon 1.txt
    I Am A Catalyst.txt
    In The Beginning 7.txt
    In The Beginning.txt
    In the Beginngins Week 5.txt
    In the Beginning Week 1.txt
    In the Beginning Week 2.txt
    In the Beginning Week 3.txt
    In the Beginning Week 5.txt
    In the Beginning week 4.txt
    Investigation Easter.txt
    James - The Primacy of Prayer - James 05-13-20.txt
    James 01-01-12 - The Method of Maturity.txt
    James 01-13-27 - The Truth About Temptation.txt
    James 02-01-13 - The Danger of Discrimination.txt
    James 03-14-04-03 - The Root of Fruit --.txt
    James 04-04-17 - The Expectation of Exclusivity.txt
    James 20-14-27 - The Witness of Works.txt
    John 1_ The Lamb of God.txt
    John 1_ The Word & The Light.txt
    John 1_ The Word Became Flesh.txt
    Jonah - Ch1 - The Depths of Disobedience.txt
    Jonah - Ch2 -  Depths of Distress.txt
    Jonah - Ch3 - Second Chances.txt
    Love Like Jesus - Discern Like Jesus.txt
    Love Like Jesus - Listen Like Jesus.txt
    Love Like Jesus - Respond Like Jesus.txt
    Love Like Jesus - Story Like Jesus.txt
    Mens.txt
    Noah and the Corrupted World.txt
    Palm Sunday 2023.txt
    Palm Sunday 2024.txt
    Parables of Jesus.txt
    Parables_ Stories of the Savior_Parables_of_the_Lost.txt
    Parables_ The Stories of the Savior_the_good_samaritan.txt
    Parables_ The Stories of the Savior_the_sower_and_the_soils.txt
    Parables_ The Stories of the Savior_the_three_servants.txt
    Parables_ The Stories of the Savior_the_unforgiving_servant.txt
    Pastor Tyler - New Year's Eve 2023.txt
    REDEEMED.txt
    RESCUE_ A Gospel Message.txt
    RESET.txt
    Revelation.txt
    Sex is Good.txt
    Stand Alones.txt
    Summer in the Psalms - Psalm 01.txt
    Summer in the Psalms - Psalm 02.txt
    Summer in the Psalms - Psalm 03.txt
    Summer in the Psalms - Psalm 08.txt
    Summer in the Psalms - Psalm 14.txt
    Summer in the Psalms - Psalm 19.txt
    TEN.txt
    The Book of 2 John.txt
    The Call.txt
    The Fossil Record.txt
    The Genesis Flood.txt
    The Holy Spirit and The Believer.txt
    The Holy Spirit in the New Testament.txt
    The Holy Spirit in the Old Testament.txt
    The Origin of the Universe.txt
    The Poison of Prosperity - James 05-01-12.txt
    The Pre-Flood World.txt
    The Primacy of Prayer 07-13-25.2676.6536.txt
    VBS Operation Creation.txt
    Vision Sunday 9-9-24.txt
    What About Dinosaurs.txt
    What is Salvation by Faith.txt
    Xmas Eve 2025.txt
library/
  __init__.py
  ingestor.py
  search.py
main.py
memory/
  .gitkeep
  CRON.md
  FILE_MAP.md
  SMOKE_TEST.md
  WATSON_ARCHITECTURE.md
  Watson_Build_List_June_29_2026.md
  architecture.md
  builds/
    20260613-164604-health-check-endpoint/
      claude-review.json
      code-diff.patch
      deployment-log.txt
      human-approval.txt
      metadata.json
      spec.md
      test-output.log
    20260613-192127-add-a-GET-/
      api/
        status-endpoint-to-jobs/
          d/
            claude-review.json
            code-diff.patch
            deployment-log.txt
            human-approval.txt
            metadata.json
            spec.md
            test-output.log
    BUILD_INDEX.md
  coding/
    _index.md
    nextjs.md
    ollama.md
    python.md
    sqlite.md
    telegram.md
  commands.json
  core.md
  projects/
    _index.md
    congregation.md
    dev_loop.md
    godfidence/
      files/
        Godfidence.pdf.pdf
      godfidence.md
    joshua_walking_in_pomise/
      joshua_walking_in_pomise.md
      memory.md
      notes/
        2026-06-06.md
    testing_project/
      testing_project.md
    twj.md
    watson/
      watson.md
    writing_room.md
  relational.md
  skills.json
  skip_keywords.txt
notes/
  .gitkeep
prompts/
  cleanup.md
  generate_blog.md
  generate_social.md
requirements.txt
run.sh
vercel.json
watson.db
```

## ~/wcky/

```
~/wcky/
.env.local
.gitattributes
.gitignore
.vercel/
  README.txt
  repo.json
content/
  blog/
    2026-04-21-your-life-is-a-passport.md
    2026-04-22-hate-in-the-heart-is-murder-and-you-probably-know-someone-you-need-to-call.md
    2026-04-24-the-biblical-definition-of-adultery-will-make-everyone-uncomfortable-equally.md
    2026-04-25-jesus-didnt-cancel-the-old-testament-he-raised-the-standard.md
    2026-04-27-you-cant-be-a-bible-thumper-and-a-kingdom-citizen-at-the-same-time.md
    2026-04-29-stop-calling-it-persecution.md
    2026-05-01-salt-that-isnt-salty-is-just-a-rock.md
    2026-05-05-the-bema-seat-why-what-you-do-in-private-actually-matters.md
    2026-05-07-god-is-not-a-genie.md
    2026-05-20-the-discernment-we-were-never-supposed-to-abandon.md
    2026-05-21-pearls-pigs-and-reading-the-room.md
    2026-05-23-the-golden-rule-is-not-a-platitude.md
    2026-05-26-the-plank-goes-first.md
    2026-05-28-rock-sand-and-the-storms-that-tell-the-truth.md
    2026-05-30-the-most-terrifying-sentence-in-scripture.md
    2026-06-02-you-can-t-fake-the-fruit.md
    2026-06-04-the-gate-nobody-wants-to-take.md
    2026-06-09-you-don-t-have-to-see-the-whole-road.md
    2026-06-10-blessing-lives-on-the-other-side-of-obedience.md
    2026-06-12-god-doesn-t-do-detours.md
    2026-06-14-stop-making-yourself-the-main-character.md
    2026-06-17-the-inheritance-you-didn-t-build.md
    2026-06-21-covenant-before-conquest.md
    2026-06-24-passion-isn-t-character.md
    2026-06-26-still-fighting-for-someone-else-s-land.md
    2026-06-28-the-inheritance-was-already-written.md
    2026-06-30-you-are-not-what-you-do.md
    the-flashlight-of-your-focus.md
    where-your-treasure-is.md
next-env.d.ts
next.config.js
package-lock.json
package.json
postcss.config.js
public/
  images/
    Bill-CR.png
    Bill-HeroRC.png
    Bill-HeroRC2.png
    Bill-RC.png
    Bill-SQ.png
    DS1-cover.png
    DS2-cover.png
    DS3-cover.png
    HeIsRisen-Cover.jpg
    lead-magnet.png
    lead-magnet2.png
    og-default.png
    og-meet.png
    wrong-jesus-cover-iso.png
  posts/
    williamckyomes.WordPress.2026-05-05.xml
scripts/
  generate-og-meet.py
src/
  app/
    about/
      page.tsx
    api/
      arc/
        apply/
          route.ts
        commitments/
          route.ts
        dashboard/
          route.ts
        feedback/
          route.ts
        login/
          route.ts
      ingest/
        route.ts
      meet/
        availability/
          route.ts
        book/
          route.ts
      read/
        [slug]/
          feedback/
            route.ts
          login/
            route.ts
          logout/
            route.ts
      room/
        admin/
          login/
            route.ts
        apply/
          route.ts
        change-password/
          route.ts
        feedback/
          route.ts
        login/
          route.ts
        logout/
          route.ts
        message/
          route.ts
        post/
          route.ts
        posts/
          route.ts
        reset/
          route.ts
        verify/
          route.ts
      submit-draft/
        route.ts.retired
      thewrongjesus/
        signup/
          route.ts
      twj/
        feedback/
          route.ts
        login/
          route.ts
        logout/
          route.ts
    apple-touch-icon.png
    arc/
      ArcSignupForm.tsx
      dashboard/
        ArcDashboard.tsx
        ManuscriptReader.tsx
        page.tsx
      login/
        ArcLoginForm.tsx
        page.tsx
      page.tsx
    blog/
      [slug]/
        page.tsx
      page.tsx
    books/
      page.tsx
    cv/
      CvDownloadButton.tsx
      cv.css
      page.tsx
    dashboard/
      page.tsx
    draft/
      page.tsx
    dreamstone/
      page.tsx
    favicon.ico
    globals.css
    ingest/
      page.tsx
    layout.tsx
    meet/
      MeetClient.tsx
      cancel/
        page.tsx
      page.tsx
    not-found.tsx
    page.tsx
    room/
      (protected)/
        PostList.tsx
        RoomNav.tsx
        RoomShell.tsx
        account/
          page.tsx
        beta/
          BetaDraftList.tsx
          page.tsx
        board/
          page.tsx
        calls/
          page.tsx
        layout.tsx
        prayer/
          page.tsx
        read/
          ArcReader.tsx
          page.tsx
        write/
          page.tsx
      LoginForm.tsx
      admin/
        login/
          page.tsx
        page.tsx
      apply/
        ApplyForm.tsx
        page.tsx
      login/
        page.tsx
      page.tsx
      reset/
        page.tsx
      verify/
        page.tsx
    speaking/
      page.tsx
    start/
      page.tsx
    theology/
      page.tsx
    thewrongjesus/
      CountdownTimer.tsx
      SignupForm.tsx
      page.tsx
    twj/
      page.tsx
      press/
        page.tsx
      read/
        LoginForm.tsx
        ManuscriptReader.tsx
        chapters/
          chapter-01.md
          chapter-02.md
          chapter-03.md
          chapter-04.md
          chapter-05.md
          chapter-06.md
          chapter-07.md
          chapter-08.md
          chapter-09.md
          chapter-10.md
          chapter-11.md
          chapter-12.md
          conclusion.md
          introduction.md
        page.tsx
  components/
    Footer.tsx
    FreeResourceButton.tsx
    Header.tsx
    HeroButtons.tsx
    HomePopup.tsx
    LeadMagnetModal.tsx
    StartCTA.tsx
  content/
    books/
      twj/
        beta/
          .gitkeep
          sample-draft.md
  lib/
    arc-api.ts
    posts.ts
    writing-room-api.ts
    writing-room-auth.ts
  middleware.ts
  types/
    index.ts
tailwind.config.ts
tsconfig.json
tsconfig.tsbuildinfo
```

## ~/watson-admin/

```
~/watson-admin/
.env.local
.gitignore
.vercel/
  README.txt
  repo.json
AGENTS.md
CLAUDE.md
README.md
app/
  (admin)/
    arc-commitments/
      page.tsx
    books/
      page.tsx
      twj/
        page.tsx
    page.tsx
    writing-room/
      page.tsx
  api/
    admin/
      arc-commitments/
        approve/
          route.ts
        invite/
          route.ts
        readers/
          route.ts
        reject/
          route.ts
      writing-room/
        approve/
          route.ts
        call/
          route.ts
        calls/
          route.ts
        deny/
          route.ts
        messages/
          route.ts
        partners/
          route.ts
        resend/
          route.ts
        reset-password/
          route.ts
        revoke/
          route.ts
        route.ts
    auth/
      login/
        route.ts
      logout/
        route.ts
    books/
      [slug]/
        route.ts
      route.ts
      twj/
        feedback/
          delete/
            route.ts
          route.ts
        readers/
          [username]/
            reset-password/
              route.ts
            route.ts
          bulk/
            route.ts
          route.ts
  favicon.ico
  globals.css
  layout.tsx
  login/
    page.tsx
components/
  AdminShell.tsx
  Sidebar.tsx
  SidebarContext.tsx
  TopBar.tsx
lib/
  auth.ts
  kv.ts
  writing-room.ts
next.config.ts
package-lock.json
package.json
postcss.config.mjs
proxy.ts
public/
  file.svg
  globe.svg
  next.svg
  vercel.svg
  window.svg
scripts/
  hash-password.js
tsconfig.json
```

## ~/watson-ui/

```
~/watson-ui/
.env.example
.gitignore
AGENTS.md
CLAUDE.md
README.md
app/
  api/
    auth/
      check/
        route.ts
      route.ts
    chat/
      route.ts
    congregation/
      [id]/
        route.ts
      route.ts
    logout/
      route.ts
    people/
      [id]/
        route.ts
      route.ts
  favicon.ico
  globals.css
  layout.tsx
  page.tsx
components/
  BriefingView.tsx
  ContactsView.tsx
  LoginScreen.tsx
  ReadingView.tsx
  SettingsView.tsx
  TasksView.tsx
eslint.config.mjs
next-env.d.ts
next.config.ts
package-lock.json
package.json
postcss.config.mjs
public/
  file.svg
  globe.svg
  manifest.json
  next.svg
  vercel.svg
  window.svg
tsconfig.json
tsconfig.tsbuildinfo
```
